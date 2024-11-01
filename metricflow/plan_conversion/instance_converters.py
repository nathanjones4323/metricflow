"""Various classes that perform a transformation on instance sets."""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass
from itertools import chain
from typing import Dict, List, Optional, Sequence, Tuple

from dbt_semantic_interfaces.references import EntityReference, MetricReference, SemanticModelReference
from dbt_semantic_interfaces.type_enums.aggregation_type import AggregationType
from dbt_semantic_interfaces.type_enums.date_part import DatePart
from dbt_semantic_interfaces.type_enums.time_granularity import TimeGranularity
from metricflow_semantics.aggregation_properties import AggregationState
from metricflow_semantics.assert_one_arg import assert_exactly_one_arg_set
from metricflow_semantics.instances import (
    DimensionInstance,
    EntityInstance,
    GroupByMetricInstance,
    InstanceSet,
    InstanceSetTransform,
    MdoInstance,
    MeasureInstance,
    MetadataInstance,
    MetricInstance,
    TimeDimensionInstance,
)
from metricflow_semantics.model.semantics.metric_lookup import MetricLookup
from metricflow_semantics.model.semantics.semantic_model_lookup import SemanticModelLookup
from metricflow_semantics.specs.column_assoc import ColumnAssociationResolver
from metricflow_semantics.specs.dimension_spec import DimensionSpec
from metricflow_semantics.specs.entity_spec import EntitySpec, LinklessEntitySpec
from metricflow_semantics.specs.group_by_metric_spec import GroupByMetricSpec
from metricflow_semantics.specs.instance_spec import InstanceSpec, LinkableInstanceSpec
from metricflow_semantics.specs.measure_spec import MeasureSpec, MetricInputMeasureSpec
from metricflow_semantics.specs.spec_set import InstanceSpecSet
from metricflow_semantics.specs.time_dimension_spec import TimeDimensionSpec
from more_itertools import bucket

from metricflow.dataflow.nodes.join_to_base import ValidityWindowJoinDescription
from metricflow.plan_conversion.select_column_gen import SelectColumnSet
from metricflow.sql.sql_exprs import (
    SqlAggregateFunctionExpression,
    SqlColumnReference,
    SqlColumnReferenceExpression,
    SqlExpressionNode,
    SqlFunction,
    SqlFunctionExpression,
    SqlStringExpression,
)
from metricflow.sql.sql_plan import (
    SqlSelectColumn,
)

logger = logging.getLogger(__name__)


class CreateSelectColumnsForInstances(InstanceSetTransform[SelectColumnSet]):
    """Create select column expressions that will express all instances in the set.

    It assumes that the column names of the instances are represented by the supplied column association resolver and
    come from the given table alias.
    """

    def __init__(
        self,
        table_alias: str,
        column_resolver: ColumnAssociationResolver,
        output_to_input_column_mapping: Optional[OrderedDict[str, str]] = None,
    ) -> None:
        """Initializer.

        Args:
            table_alias: the table alias to select columns from
            column_resolver: resolver to name columns.
            output_to_input_column_mapping: if specified, use these columns in the input for the given output columns.
        """
        self._table_alias = table_alias
        self._column_resolver = column_resolver
        self._output_to_input_column_mapping = output_to_input_column_mapping or OrderedDict()

    def transform(self, instance_set: InstanceSet) -> SelectColumnSet:  # noqa: D102
        metric_cols = list(
            chain.from_iterable([self._make_sql_column_expression(x) for x in instance_set.metric_instances])
        )
        measure_cols = list(
            chain.from_iterable([self._make_sql_column_expression(x) for x in instance_set.measure_instances])
        )
        dimension_cols = list(
            chain.from_iterable([self._make_sql_column_expression(x) for x in instance_set.dimension_instances])
        )
        time_dimension_cols = list(
            chain.from_iterable([self._make_sql_column_expression(x) for x in instance_set.time_dimension_instances])
        )
        entity_cols = list(
            chain.from_iterable([self._make_sql_column_expression(x) for x in instance_set.entity_instances])
        )
        metadata_cols = list(
            chain.from_iterable([self._make_sql_column_expression(x) for x in instance_set.metadata_instances])
        )
        group_by_metric_cols = list(
            chain.from_iterable([self._make_sql_column_expression(x) for x in instance_set.group_by_metric_instances])
        )
        return SelectColumnSet(
            metric_columns=metric_cols,
            measure_columns=measure_cols,
            dimension_columns=dimension_cols,
            time_dimension_columns=time_dimension_cols,
            entity_columns=entity_cols,
            group_by_metric_columns=group_by_metric_cols,
            metadata_columns=metadata_cols,
        )

    def _make_sql_column_expression(
        self,
        element_instance: MdoInstance,
    ) -> List[SqlSelectColumn]:
        """Convert one element instance into a SQL column."""
        # Do a sanity check to make sure that there's a 1:1 mapping between the columns associations generated by the
        # column resolver based on the spec, and the columns that are already associated with the instance.
        expected_column_associations = (self._column_resolver.resolve_spec(element_instance.spec),)
        existing_column_associations = element_instance.associated_columns

        # Dict between the expected column name and the corresponding column in the existing columns
        column_matches: Dict[str, List[str]] = {
            expected_column.column_name: [
                col.column_name
                for col in existing_column_associations
                if col.column_correlation_key == expected_column.column_correlation_key
            ]
            for expected_column in expected_column_associations
        }

        # Assert a 1:1 mapping between expected and existing
        assert all([len(x) == 1 for x in column_matches.values()]), (
            f"Did not find exactly one match for each expected column associations.  "
            f"Expected -> existing mappings: {column_matches}"
        )
        existing_names = set([col.column_name for col in existing_column_associations])
        mapped_names = set()
        mapped_cols: List[str] = []
        for mapped_cols in column_matches.values():
            mapped_names.update([col_name for col_name in mapped_cols])
        assert existing_names == mapped_names, (
            f"Not all existing columns were mapped. Existing: {existing_names}.  Mapped: {mapped_cols}, "
            f"{expected_column_associations} -- {existing_column_associations}"
        )

        select_columns = []
        for expected_name, mapped_cols in column_matches.items():
            input_column_name = mapped_cols[0]
            output_column_name = expected_name

            if output_column_name in self._output_to_input_column_mapping:
                input_column_name = self._output_to_input_column_mapping[output_column_name]
            select_columns.append(
                SqlSelectColumn(
                    expr=SqlColumnReferenceExpression.create(SqlColumnReference(self._table_alias, input_column_name)),
                    column_alias=output_column_name,
                )
            )
        return select_columns


class CreateSelectColumnsWithMeasuresAggregated(CreateSelectColumnsForInstances):
    """Create select columns of the form "fct_bookings.bookings AS bookings" for all the instances.

    However, for measure columns, convert them into expressions like "SUM(fct_bookings.bookings) AS bookings" so that
    the resulting expressions can be used for aggregations.

    Also add an output alias that conforms to the alias
    """

    def __init__(  # noqa: D107
        self,
        table_alias: str,
        column_resolver: ColumnAssociationResolver,
        semantic_model_lookup: SemanticModelLookup,
        metric_input_measure_specs: Sequence[MetricInputMeasureSpec],
    ) -> None:
        self._semantic_model_lookup = semantic_model_lookup
        self.metric_input_measure_specs = metric_input_measure_specs
        super().__init__(table_alias=table_alias, column_resolver=column_resolver)

    def _make_sql_column_expression_to_aggregate_measures(
        self, measure_instances: Tuple[MeasureInstance, ...]
    ) -> List[SqlSelectColumn]:
        output_columns: List[SqlSelectColumn] = []
        aliased_input_specs = [spec for spec in self.metric_input_measure_specs if spec.alias]
        for instance in measure_instances:
            matches = [spec for spec in aliased_input_specs if spec.measure_spec == instance.spec]
            if matches:
                aliased_spec = matches[0]
                aliased_input_specs.remove(aliased_spec)
                output_measure_spec = aliased_spec.post_aggregation_spec
            else:
                output_measure_spec = instance.spec

            output_columns.append(
                self._make_sql_column_expression_to_aggregate_measure(
                    measure_instance=instance, output_measure_spec=output_measure_spec
                )
            )

        return output_columns

    def _make_sql_column_expression_to_aggregate_measure(
        self, measure_instance: MeasureInstance, output_measure_spec: MeasureSpec
    ) -> SqlSelectColumn:
        """Convert one measure instance into a SQL column."""
        # Get the column name of the measure in the table that we're reading from
        column_name_in_table = measure_instance.associated_column.column_name

        # Create an expression that will aggregate the given measure.
        # Figure out the aggregation function for the measure.
        measure = self._semantic_model_lookup.measure_lookup.get_measure(measure_instance.spec.reference)
        aggregation_type = measure.agg

        expression_to_get_measure = SqlColumnReferenceExpression.create(
            SqlColumnReference(self._table_alias, column_name_in_table)
        )

        expression_to_aggregate_measure = SqlFunctionExpression.build_expression_from_aggregation_type(
            aggregation_type=aggregation_type,
            sql_column_expression=expression_to_get_measure,
            agg_params=measure.agg_params,
        )

        # Get the output column name from the measure/alias

        new_column_association_for_aggregated_measure = self._column_resolver.resolve_spec(output_measure_spec)
        new_column_name_for_aggregated_measure = new_column_association_for_aggregated_measure.column_name

        return SqlSelectColumn(
            expr=expression_to_aggregate_measure,
            column_alias=new_column_name_for_aggregated_measure,
        )

    def transform(self, instance_set: InstanceSet) -> SelectColumnSet:  # noqa: D102
        metric_cols = list(
            chain.from_iterable([self._make_sql_column_expression(x) for x in instance_set.metric_instances])
        )

        measure_cols = self._make_sql_column_expression_to_aggregate_measures(instance_set.measure_instances)
        dimension_cols = list(
            chain.from_iterable([self._make_sql_column_expression(x) for x in instance_set.dimension_instances])
        )
        time_dimension_cols = list(
            chain.from_iterable([self._make_sql_column_expression(x) for x in instance_set.time_dimension_instances])
        )
        entity_cols = list(
            chain.from_iterable([self._make_sql_column_expression(x) for x in instance_set.entity_instances])
        )
        metadata_cols = list(
            chain.from_iterable([self._make_sql_column_expression(x) for x in instance_set.metadata_instances])
        )
        group_by_metric_cols = list(
            chain.from_iterable([self._make_sql_column_expression(x) for x in instance_set.group_by_metric_instances])
        )
        return SelectColumnSet(
            metric_columns=metric_cols,
            measure_columns=measure_cols,
            dimension_columns=dimension_cols,
            time_dimension_columns=time_dimension_cols,
            entity_columns=entity_cols,
            group_by_metric_columns=group_by_metric_cols,
            metadata_columns=metadata_cols,
        )


@dataclass(frozen=True)
class _DimensionValidityParams:
    """Helper dataclass for managing dimension validity properties."""

    dimension_name: str
    time_granularity: TimeGranularity
    date_part: Optional[DatePart] = None


class CreateValidityWindowJoinDescription(InstanceSetTransform[Optional[ValidityWindowJoinDescription]]):
    """Create and return a ValidityWindowJoinDescription based on the given InstanceSet.

    During join resolution we need to determine whether or not a given data set represents a
    Type II SCD dataset - i.e., one with a validity window defined on each row. This requires
    checking the set of dimension instances and determining whether or not those originate from
    an SCD source, and extracting validity window information accordingly.
    """

    def __init__(self, semantic_model_lookup: SemanticModelLookup) -> None:
        """Initializer. The SemanticModelLookup is needed for getting the original model definition."""
        self._semantic_model_lookup = semantic_model_lookup

    def _get_validity_window_dimensions_for_semantic_model(
        self, semantic_model_reference: SemanticModelReference
    ) -> Optional[Tuple[_DimensionValidityParams, _DimensionValidityParams]]:
        """Returns a 2-tuple (start, end) of validity window dimensions info, if any exist in the semantic model."""
        semantic_model = self._semantic_model_lookup.get_by_reference(semantic_model_reference)
        assert semantic_model, f"Could not find semantic model {semantic_model_reference} after data set conversion!"

        start_dim = semantic_model.validity_start_dimension
        end_dim = semantic_model.validity_end_dimension

        # We do this instead of relying on has_validity_dimensions because this also does type refinement
        if not start_dim or not end_dim:
            return None

        assert start_dim.type_params, "Typechecker hint - validity info cannot exist without type params"
        assert end_dim.type_params, "Typechecker hint - validity info cannot exist without type params"

        return (
            _DimensionValidityParams(
                dimension_name=start_dim.name, time_granularity=start_dim.type_params.time_granularity
            ),
            _DimensionValidityParams(
                dimension_name=end_dim.name, time_granularity=end_dim.type_params.time_granularity
            ),
        )

    def transform(self, instance_set: InstanceSet) -> Optional[ValidityWindowJoinDescription]:
        """Find the Time Dimension specs defining a validity window, if any, and return it.

        This currently throws an exception if more than one such window is found, and effectively prevents
        us from processing a dataset composed of a join between two SCD semantic models. This restriction is in
        place as a temporary simplification - if there is need for this feature we can enable it.
        """
        semantic_model_to_window: Dict[SemanticModelReference, ValidityWindowJoinDescription] = {}
        instances_by_semantic_model = bucket(
            instance_set.time_dimension_instances, lambda x: x.origin_semantic_model_reference.semantic_model_reference
        )
        for semantic_model_reference in instances_by_semantic_model:
            validity_dims = self._get_validity_window_dimensions_for_semantic_model(semantic_model_reference)
            if validity_dims is None:
                continue

            start_dim, end_dim = validity_dims
            specs = {instance.spec for instance in instances_by_semantic_model[semantic_model_reference]}
            start_specs = [
                spec
                for spec in specs
                if spec.element_name == start_dim.dimension_name
                # TODO: [custom_granularity] - support custom granularities for SCDs. Note this requires
                # addition of SCD support for window sub-selection, similar to what we do for cumulative metrics
                # when we group by a different time grain (e.g., select last_value from window, etc.)
                and not spec.time_granularity.is_custom_granularity
                and spec.time_granularity.base_granularity == start_dim.time_granularity
                and spec.date_part == start_dim.date_part
            ]
            end_specs = [
                spec
                for spec in specs
                if spec.element_name == end_dim.dimension_name
                # TODO: [custom_granularity] - support custom granularities for SCDs. Note this requires
                # addition of SCD support for window sub-selection, similar to what we do for cumulative metrics
                # when we group by a different time grain (e.g., select last_value from window, etc.)
                and not spec.time_granularity.is_custom_granularity
                and spec.time_granularity.base_granularity == end_dim.time_granularity
                and spec.date_part == end_dim.date_part
            ]
            linkless_start_specs = {spec.without_entity_links for spec in start_specs}
            linkless_end_specs = {spec.without_entity_links for spec in end_specs}
            assert len(linkless_start_specs) == 1 and len(linkless_end_specs) == 1, (
                f"Did not find exactly one pair of specs from semantic model `{semantic_model_reference}` matching the validity "
                f"window end points defined in the semantic model. This means we cannot process an SCD join, because we "
                f"require exactly one validity window to be specified for the query! The window in the semantic model "
                f"is defined by start dimension `{start_dim}` and end dimension `{end_dim}`. We found "
                f"{len(linkless_start_specs)} linkless specs for window start ({linkless_start_specs}) and "
                f"{len(linkless_end_specs)} linkless specs for window end ({linkless_end_specs})."
            )
            # SCD join targets are joined as dimension links in much the same was as partitions are joined. Therefore,
            # we treat this like a partition time column join and take the dimension spec with the shortest set of
            # entity links so that the subquery uses the correct reference in the ON statement
            start_specs = sorted(start_specs, key=lambda x: len(x.entity_links))
            end_specs = sorted(end_specs, key=lambda x: len(x.entity_links))
            semantic_model_to_window[semantic_model_reference] = ValidityWindowJoinDescription(
                window_start_dimension=start_specs[0], window_end_dimension=end_specs[0]
            )

        assert len(semantic_model_to_window) < 2, (
            f"Found more than 1 set of validity window specs in the input instance set. This is not currently "
            f"supported, as joins between SCD semantic models are not yet allowed! {semantic_model_to_window}"
        )

        if semantic_model_to_window:
            return list(semantic_model_to_window.values())[0]

        return None


class AddLinkToLinkableElements(InstanceSetTransform[InstanceSet]):
    """Return a new instance set where the all linkable elements in the set have a new link added.

    e.g. "country" -> "user_id__country" after a data set has been joined by entity.
    """

    def __init__(self, join_on_entity: LinklessEntitySpec) -> None:  # noqa: D107
        self._join_on_entity = join_on_entity

    def transform(self, instance_set: InstanceSet) -> InstanceSet:  # noqa: D102
        assert len(instance_set.metric_instances) == 0, "Can't add links to instance sets with metrics"
        assert len(instance_set.measure_instances) == 0, "Can't add links to instance sets with measures"

        # Handle dimension instances
        dimension_instances_with_additional_link = []
        for dimension_instance in instance_set.dimension_instances:
            # The new dimension spec should include the join on entity.
            transformed_dimension_spec_from_right = DimensionSpec(
                element_name=dimension_instance.spec.element_name,
                entity_links=self._join_on_entity.as_linkless_prefix + dimension_instance.spec.entity_links,
            )
            dimension_instances_with_additional_link.append(
                DimensionInstance(
                    associated_columns=dimension_instance.associated_columns,
                    defined_from=dimension_instance.defined_from,
                    spec=transformed_dimension_spec_from_right,
                )
            )

        # Handle time dimension instances
        time_dimension_instances_with_additional_link = []
        for time_dimension_instance in instance_set.time_dimension_instances:
            # The new dimension spec should include the join on entity.
            transformed_time_dimension_spec_from_right = TimeDimensionSpec(
                element_name=time_dimension_instance.spec.element_name,
                entity_links=(
                    (EntityReference(element_name=self._join_on_entity.element_name),)
                    + time_dimension_instance.spec.entity_links
                ),
                time_granularity=time_dimension_instance.spec.time_granularity,
                date_part=time_dimension_instance.spec.date_part,
            )
            time_dimension_instances_with_additional_link.append(
                TimeDimensionInstance(
                    associated_columns=time_dimension_instance.associated_columns,
                    defined_from=time_dimension_instance.defined_from,
                    spec=transformed_time_dimension_spec_from_right,
                )
            )

        # Handle entity instances
        entity_instances_with_additional_link = []
        for entity_instance in instance_set.entity_instances:
            # Don't include adding the entity link to the same entity.
            # Otherwise, you would create "user_id__user_id", which is confusing.
            if entity_instance.spec == self._join_on_entity:
                continue
            # The new entity spec should include the join on entity.
            transformed_entity_spec_from_right = EntitySpec(
                element_name=entity_instance.spec.element_name,
                entity_links=self._join_on_entity.as_linkless_prefix + entity_instance.spec.entity_links,
            )
            entity_instances_with_additional_link.append(
                EntityInstance(
                    associated_columns=entity_instance.associated_columns,
                    defined_from=entity_instance.defined_from,
                    spec=transformed_entity_spec_from_right,
                )
            )

        # Handle group by metric instances
        group_by_metric_instances_with_additional_link = []
        for group_by_metric_instance in instance_set.group_by_metric_instances:
            transformed_group_by_metric_spec_from_right = GroupByMetricSpec(
                element_name=group_by_metric_instance.spec.element_name,
                entity_links=self._join_on_entity.as_linkless_prefix + group_by_metric_instance.spec.entity_links,
                metric_subquery_entity_links=group_by_metric_instance.spec.metric_subquery_entity_links,
            )
            group_by_metric_instances_with_additional_link.append(
                GroupByMetricInstance(
                    associated_columns=group_by_metric_instance.associated_columns,
                    defined_from=group_by_metric_instance.defined_from,
                    spec=transformed_group_by_metric_spec_from_right,
                )
            )

        return InstanceSet(
            measure_instances=(),
            dimension_instances=tuple(dimension_instances_with_additional_link),
            time_dimension_instances=tuple(time_dimension_instances_with_additional_link),
            entity_instances=tuple(entity_instances_with_additional_link),
            group_by_metric_instances=tuple(group_by_metric_instances_with_additional_link),
            metric_instances=(),
            metadata_instances=(),
        )


class FilterLinkableInstancesWithLeadingLink(InstanceSetTransform[InstanceSet]):
    """Return an instance set with the elements that have a specified leading link removed.

    e.g. Remove "listing__country" if the specified link is "listing".
    """

    def __init__(
        self,
        entity_link: LinklessEntitySpec,
    ) -> None:
        """Constructor.

        Args:
            entity_link: Remove elements with this link as the first element in "entity_links"
        """
        self._entity_link = entity_link

    def _should_pass(self, linkable_spec: LinkableInstanceSpec) -> bool:
        return (
            len(linkable_spec.entity_links) == 0
            or LinklessEntitySpec.from_reference(linkable_spec.entity_links[0]) != self._entity_link
        )

    def transform(self, instance_set: InstanceSet) -> InstanceSet:  # noqa: D102
        # Normal to not filter anything if the instance set has no instances with links.
        filtered_dimension_instances = tuple(x for x in instance_set.dimension_instances if self._should_pass(x.spec))
        filtered_time_dimension_instances = tuple(
            x for x in instance_set.time_dimension_instances if self._should_pass(x.spec)
        )
        filtered_entity_instances = tuple(x for x in instance_set.entity_instances if self._should_pass(x.spec))
        filtered_group_by_metric_instances = tuple(
            x for x in instance_set.group_by_metric_instances if self._should_pass(x.spec)
        )

        output = InstanceSet(
            measure_instances=instance_set.measure_instances,
            dimension_instances=filtered_dimension_instances,
            time_dimension_instances=filtered_time_dimension_instances,
            entity_instances=filtered_entity_instances,
            group_by_metric_instances=filtered_group_by_metric_instances,
            metric_instances=instance_set.metric_instances,
            metadata_instances=instance_set.metadata_instances,
        )
        return output


class FilterElements(InstanceSetTransform[InstanceSet]):
    """Return an instance set with the elements that don't match any of the pass specs removed."""

    def __init__(
        self,
        include_specs: Optional[InstanceSpecSet] = None,
        exclude_specs: Optional[InstanceSpecSet] = None,
    ) -> None:
        """Constructor.

        Args:
            include_specs: If specified, pass only instances matching these specs.
            exclude_specs: If specified, pass only instances not matching these specs.
        """
        assert_exactly_one_arg_set(include_specs=include_specs, exclude_specs=exclude_specs)
        self._include_specs = include_specs
        self._exclude_specs = exclude_specs

    def _should_pass(self, element_spec: InstanceSpec) -> bool:
        # TODO: Use better matching function
        if self._include_specs:
            return any(x == element_spec for x in self._include_specs.all_specs)
        elif self._exclude_specs:
            return not any(x == element_spec for x in self._exclude_specs.all_specs)
        assert False

    def transform(self, instance_set: InstanceSet) -> InstanceSet:  # noqa: D102
        # Sanity check to make sure the specs are in the instance set

        if self._include_specs:
            include_specs_not_found = []
            for include_spec in self._include_specs.all_specs:
                if include_spec not in instance_set.spec_set.all_specs:
                    include_specs_not_found.append(include_spec)
            if include_specs_not_found:
                raise RuntimeError(
                    f"Include specs {include_specs_not_found} are not in the spec set {instance_set.spec_set} - "
                    f"check if this node was constructed correctly."
                )
        elif self._exclude_specs:
            exclude_specs_not_found = []
            for exclude_spec in self._exclude_specs.all_specs:
                if exclude_spec not in instance_set.spec_set.all_specs:
                    exclude_specs_not_found.append(exclude_spec)
            if exclude_specs_not_found:
                raise RuntimeError(
                    f"Exclude specs {exclude_specs_not_found} are not in the spec set {instance_set.spec_set} - "
                    f"check if this node was constructed correctly."
                )
        else:
            assert False, "Include specs or exclude specs should have been specified."

        output = InstanceSet(
            measure_instances=tuple(x for x in instance_set.measure_instances if self._should_pass(x.spec)),
            dimension_instances=tuple(x for x in instance_set.dimension_instances if self._should_pass(x.spec)),
            time_dimension_instances=tuple(
                x for x in instance_set.time_dimension_instances if self._should_pass(x.spec)
            ),
            entity_instances=tuple(x for x in instance_set.entity_instances if self._should_pass(x.spec)),
            group_by_metric_instances=tuple(
                x for x in instance_set.group_by_metric_instances if self._should_pass(x.spec)
            ),
            metric_instances=tuple(x for x in instance_set.metric_instances if self._should_pass(x.spec)),
            metadata_instances=tuple(x for x in instance_set.metadata_instances if self._should_pass(x.spec)),
        )
        return output


class ChangeMeasureAggregationState(InstanceSetTransform[InstanceSet]):
    """Returns a new instance set where all measures are set as a different aggregation state."""

    def __init__(self, aggregation_state_changes: Dict[AggregationState, AggregationState]) -> None:
        """Constructor.

        Args:
            aggregation_state_changes: key is old aggregation state, value is the new aggregation state.
        """
        self._aggregation_state_changes = aggregation_state_changes

    def transform(self, instance_set: InstanceSet) -> InstanceSet:  # noqa: D102
        for measure_instance in instance_set.measure_instances:
            assert measure_instance.aggregation_state in self._aggregation_state_changes, (
                f"Aggregation state: {measure_instance.aggregation_state} not handled in change dict: "
                f"{self._aggregation_state_changes}"
            )

        # Copy the measures, but just change the aggregation state to COMPLETE.
        measure_instances = tuple(
            MeasureInstance(
                associated_columns=x.associated_columns,
                defined_from=x.defined_from,
                aggregation_state=self._aggregation_state_changes[x.aggregation_state],
                spec=x.spec,
            )
            for x in instance_set.measure_instances
        )
        return InstanceSet(
            measure_instances=measure_instances,
            dimension_instances=instance_set.dimension_instances,
            time_dimension_instances=instance_set.time_dimension_instances,
            entity_instances=instance_set.entity_instances,
            group_by_metric_instances=instance_set.group_by_metric_instances,
            metric_instances=instance_set.metric_instances,
            metadata_instances=instance_set.metadata_instances,
        )


class UpdateMeasureFillNullsWith(InstanceSetTransform[InstanceSet]):
    """Returns a new instance set where all measures have been assigned the fill nulls with property."""

    def __init__(self, metric_input_measure_specs: Sequence[MetricInputMeasureSpec]):
        """Initializer stores the input specs, which contain the fill_nulls_with for each measure."""
        self.metric_input_measure_specs = metric_input_measure_specs

    def _update_fill_nulls_with(self, measure_instances: Tuple[MeasureInstance, ...]) -> Tuple[MeasureInstance, ...]:
        """Update all measure instances with the corresponding fill_nulls_with value."""
        updated_instances: List[MeasureInstance] = []
        for instance in measure_instances:
            # There might be multiple matching specs, but they'll all have the same fill_nulls_with value so we can use any of them.
            matching_specs = [spec for spec in self.metric_input_measure_specs if spec.measure_spec == instance.spec]
            assert (
                len(matching_specs) >= 1
            ), f"Found no matching input measure spec for measure instance {instance}. Something has been misconfigured."

            measure_spec = MeasureSpec(
                element_name=instance.spec.element_name,
                fill_nulls_with=matching_specs[0].fill_nulls_with,
                non_additive_dimension_spec=instance.spec.non_additive_dimension_spec,
            )
            updated_instances.append(
                MeasureInstance(
                    associated_columns=instance.associated_columns,
                    spec=measure_spec,
                    aggregation_state=instance.aggregation_state,
                    defined_from=instance.defined_from,
                )
            )

        return tuple(updated_instances)

    def transform(self, instance_set: InstanceSet) -> InstanceSet:  # noqa: D102
        return InstanceSet(
            measure_instances=self._update_fill_nulls_with(instance_set.measure_instances),
            dimension_instances=instance_set.dimension_instances,
            time_dimension_instances=instance_set.time_dimension_instances,
            entity_instances=instance_set.entity_instances,
            group_by_metric_instances=instance_set.group_by_metric_instances,
            metric_instances=instance_set.metric_instances,
            metadata_instances=instance_set.metadata_instances,
        )


class AliasAggregatedMeasures(InstanceSetTransform[InstanceSet]):
    """Returns a new instance set where all measures have been assigned an alias spec."""

    def __init__(self, metric_input_measure_specs: Sequence[MetricInputMeasureSpec]):
        """Initializer stores the input specs, which contain the aliases for each measure.

        Note this class only works if used in conjunction with an AggregateMeasuresNode that has been generated
        by querying a single semantic model for a single set of aggregated measures. This is currently enforced
        by the structure of the DataflowPlanBuilder, which ensures each AggregateMeasuresNode corresponds to
        a single semantic model set of measures for a single metric, and that these outputs will then be
        combinded via joins.
        """
        self.metric_input_measure_specs = metric_input_measure_specs

    def _alias_measure_instances(self, measure_instances: Tuple[MeasureInstance, ...]) -> Tuple[MeasureInstance, ...]:
        """Update all measure instances with aliases, if any are found in the input spec set."""
        aliased_instances: List[MeasureInstance] = []
        aliased_input_specs = [spec for spec in self.metric_input_measure_specs if spec.alias]
        for instance in measure_instances:
            matches = [spec for spec in aliased_input_specs if spec.measure_spec == instance.spec]
            assert (
                len(matches) < 2
            ), f"Found duplicate aliased measure spec matches: {matches} for measure instance {instance}. "
            "We should always have 0 or 1 matches, or else we might pass the wrong aggregated measures to the "
            "downstream metric computation expression!"
            if matches:
                aliased_spec = matches[0]
                aliased_input_specs.remove(aliased_spec)
                measure_spec = aliased_spec.post_aggregation_spec
            else:
                measure_spec = instance.spec

            aliased_instances.append(
                MeasureInstance(
                    associated_columns=instance.associated_columns,
                    spec=measure_spec,
                    aggregation_state=instance.aggregation_state,
                    defined_from=instance.defined_from,
                )
            )

        return tuple(aliased_instances)

    def transform(self, instance_set: InstanceSet) -> InstanceSet:  # noqa: D102
        return InstanceSet(
            measure_instances=self._alias_measure_instances(instance_set.measure_instances),
            dimension_instances=instance_set.dimension_instances,
            time_dimension_instances=instance_set.time_dimension_instances,
            entity_instances=instance_set.entity_instances,
            group_by_metric_instances=instance_set.group_by_metric_instances,
            metric_instances=instance_set.metric_instances,
            metadata_instances=instance_set.metadata_instances,
        )


class AddMetrics(InstanceSetTransform[InstanceSet]):
    """Adds the given metric instances to the instance set."""

    def __init__(self, metric_instances: List[MetricInstance]) -> None:  # noqa: D107
        self._metric_instances = metric_instances

    def transform(self, instance_set: InstanceSet) -> InstanceSet:  # noqa: D102
        return InstanceSet(
            measure_instances=instance_set.measure_instances,
            dimension_instances=instance_set.dimension_instances,
            time_dimension_instances=instance_set.time_dimension_instances,
            entity_instances=instance_set.entity_instances,
            group_by_metric_instances=instance_set.group_by_metric_instances,
            metric_instances=instance_set.metric_instances + tuple(self._metric_instances),
            metadata_instances=instance_set.metadata_instances,
        )


class AddGroupByMetric(InstanceSetTransform[InstanceSet]):
    """Adds the given metric instances to the instance set."""

    def __init__(self, group_by_metric_instance: GroupByMetricInstance) -> None:  # noqa: D107
        self._group_by_metric_instance = group_by_metric_instance

    def transform(self, instance_set: InstanceSet) -> InstanceSet:  # noqa: D102
        return InstanceSet(
            measure_instances=instance_set.measure_instances,
            dimension_instances=instance_set.dimension_instances,
            time_dimension_instances=instance_set.time_dimension_instances,
            entity_instances=instance_set.entity_instances,
            group_by_metric_instances=instance_set.group_by_metric_instances + (self._group_by_metric_instance,),
            metric_instances=instance_set.metric_instances,
            metadata_instances=instance_set.metadata_instances,
        )


class RemoveMeasures(InstanceSetTransform[InstanceSet]):
    """Remove measures from the instance set."""

    def transform(self, instance_set: InstanceSet) -> InstanceSet:  # noqa: D102
        return InstanceSet(
            measure_instances=(),
            dimension_instances=instance_set.dimension_instances,
            time_dimension_instances=instance_set.time_dimension_instances,
            entity_instances=instance_set.entity_instances,
            group_by_metric_instances=instance_set.group_by_metric_instances,
            metric_instances=instance_set.metric_instances,
            metadata_instances=instance_set.metadata_instances,
        )


class RemoveMetrics(InstanceSetTransform[InstanceSet]):
    """Remove metrics from the instance set."""

    def transform(self, instance_set: InstanceSet) -> InstanceSet:  # noqa: D102
        return InstanceSet(
            measure_instances=instance_set.measure_instances,
            dimension_instances=instance_set.dimension_instances,
            time_dimension_instances=instance_set.time_dimension_instances,
            entity_instances=instance_set.entity_instances,
            group_by_metric_instances=instance_set.group_by_metric_instances,
            metric_instances=(),
            metadata_instances=instance_set.metadata_instances,
        )


class CreateSqlColumnReferencesForInstances(InstanceSetTransform[Tuple[SqlColumnReferenceExpression, ...]]):
    """Create select column expressions that will express all instances in the set.

    It assumes that the column names of the instances are represented by the supplied column association resolver and
    come from the given table alias.
    """

    def __init__(
        self,
        table_alias: str,
        column_resolver: ColumnAssociationResolver,
    ) -> None:
        """Initializer.

        Args:
            table_alias: the table alias to select columns from
            column_resolver: resolver to name columns.
        """
        self._table_alias = table_alias
        self._column_resolver = column_resolver

    def transform(self, instance_set: InstanceSet) -> Tuple[SqlColumnReferenceExpression, ...]:  # noqa: D102
        column_names = [
            self._column_resolver.resolve_spec(spec).column_name for spec in instance_set.spec_set.all_specs
        ]
        return tuple(
            SqlColumnReferenceExpression.create(
                SqlColumnReference(
                    table_alias=self._table_alias,
                    column_name=column_name,
                ),
            )
            for column_name in column_names
        )


class CreateSelectColumnForCombineOutputNode(InstanceSetTransform[SelectColumnSet]):
    """Create select column expressions for the instance for joining outputs.

    It assumes that the column names of the instances are represented by the supplied column association resolver and
    come from the given table alias.
    """

    def __init__(  # noqa: D107
        self,
        table_alias: str,
        column_resolver: ColumnAssociationResolver,
        metric_lookup: MetricLookup,
    ) -> None:
        self._table_alias = table_alias
        self._column_resolver = column_resolver
        self._metric_lookup = metric_lookup

    def _create_select_column(self, spec: InstanceSpec, fill_nulls_with: Optional[int] = None) -> SqlSelectColumn:
        """Creates the select column for the given spec and the fill value."""
        column_name = self._column_resolver.resolve_spec(spec).column_name
        column_reference_expression = SqlColumnReferenceExpression.create(
            col_ref=SqlColumnReference(
                table_alias=self._table_alias,
                column_name=column_name,
            )
        )
        select_expression: SqlExpressionNode = SqlFunctionExpression.build_expression_from_aggregation_type(
            aggregation_type=AggregationType.MAX, sql_column_expression=column_reference_expression
        )
        if fill_nulls_with is not None:
            select_expression = SqlAggregateFunctionExpression.create(
                sql_function=SqlFunction.COALESCE,
                sql_function_args=[
                    select_expression,
                    SqlStringExpression.create(str(fill_nulls_with)),
                ],
            )
        return SqlSelectColumn(
            expr=select_expression,
            column_alias=column_name,
        )

    def _create_select_columns_for_metrics(self, metric_instances: Tuple[MetricInstance, ...]) -> List[SqlSelectColumn]:
        select_columns: List[SqlSelectColumn] = []
        for metric_instance in metric_instances:
            metric_reference = MetricReference(element_name=metric_instance.defined_from.metric_name)
            input_measure = self._metric_lookup.configured_input_measure_for_metric(metric_reference=metric_reference)
            fill_nulls_with: Optional[int] = None
            if input_measure and input_measure.fill_nulls_with is not None:
                fill_nulls_with = input_measure.fill_nulls_with
            select_columns.append(
                self._create_select_column(spec=metric_instance.spec, fill_nulls_with=fill_nulls_with)
            )
        return select_columns

    def _create_select_columns_for_measures(
        self, measure_instances: Tuple[MeasureInstance, ...]
    ) -> List[SqlSelectColumn]:
        select_columns: List[SqlSelectColumn] = []
        for measure_instance in measure_instances:
            measure_spec = measure_instance.spec
            select_columns.append(
                self._create_select_column(spec=measure_spec, fill_nulls_with=measure_spec.fill_nulls_with)
            )
        return select_columns

    def transform(self, instance_set: InstanceSet) -> SelectColumnSet:  # noqa: D102
        return SelectColumnSet(
            metric_columns=self._create_select_columns_for_metrics(instance_set.metric_instances),
            measure_columns=self._create_select_columns_for_measures(instance_set.measure_instances),
        )


class ChangeAssociatedColumns(InstanceSetTransform[InstanceSet]):
    """Change the columns associated with instances to the one specified by the resolver.

    This is useful for conveniently generating output instances for a node that serve as a "pass-through". The output
    instances can be a copy of the parent's instances, except that the column names need to be changed.

    e.g. the parent may have a data set:
        sql:
            SELECT
                is_lux AS is_lux_latext
            ...
        instance:
            DimensionInstance(column_name="is_lux", ...)

    but for the current node, we want a data set like:

        sql:
            SELECT
                is_lux_latest
                ...
            FROM (
                -- SQL from parent
                is_lux AS is_lux_latest
                ...
            )
            ...
        instance:
            DimensionInstance(column_name="is_lux_latest")
    """

    def __init__(self, column_association_resolver: ColumnAssociationResolver) -> None:  # noqa: D107
        self._column_association_resolver = column_association_resolver

    def transform(self, instance_set: InstanceSet) -> InstanceSet:  # noqa: D102
        output_measure_instances = []
        for input_measure_instance in instance_set.measure_instances:
            output_measure_instances.append(
                MeasureInstance(
                    associated_columns=(self._column_association_resolver.resolve_spec(input_measure_instance.spec),),
                    spec=input_measure_instance.spec,
                    defined_from=input_measure_instance.defined_from,
                    aggregation_state=input_measure_instance.aggregation_state,
                )
            )

        output_dimension_instances = []
        for input_dimension_instance in instance_set.dimension_instances:
            output_dimension_instances.append(
                DimensionInstance(
                    associated_columns=(self._column_association_resolver.resolve_spec(input_dimension_instance.spec),),
                    spec=input_dimension_instance.spec,
                    defined_from=input_dimension_instance.defined_from,
                )
            )

        output_time_dimension_instances = []
        for input_time_dimension_instance in instance_set.time_dimension_instances:
            output_time_dimension_instances.append(
                TimeDimensionInstance(
                    associated_columns=(
                        self._column_association_resolver.resolve_spec(input_time_dimension_instance.spec),
                    ),
                    spec=input_time_dimension_instance.spec,
                    defined_from=input_time_dimension_instance.defined_from,
                )
            )

        output_entity_instances = []
        for input_entity_instance in instance_set.entity_instances:
            output_entity_instances.append(
                EntityInstance(
                    associated_columns=(self._column_association_resolver.resolve_spec(input_entity_instance.spec),),
                    spec=input_entity_instance.spec,
                    defined_from=input_entity_instance.defined_from,
                )
            )

        output_metric_instances = []
        for input_metric_instance in instance_set.metric_instances:
            output_metric_instances.append(
                MetricInstance(
                    associated_columns=(self._column_association_resolver.resolve_spec(input_metric_instance.spec),),
                    spec=input_metric_instance.spec,
                    defined_from=input_metric_instance.defined_from,
                )
            )

        output_metadata_instances = []
        for input_metadata_instance in instance_set.metadata_instances:
            output_metadata_instances.append(
                MetadataInstance(
                    associated_columns=(self._column_association_resolver.resolve_spec(input_metadata_instance.spec),),
                    spec=input_metadata_instance.spec,
                )
            )

        output_group_by_metric_instances = []
        for input_group_by_metric_instance in instance_set.group_by_metric_instances:
            output_group_by_metric_instances.append(
                GroupByMetricInstance(
                    associated_columns=(
                        self._column_association_resolver.resolve_spec(input_group_by_metric_instance.spec),
                    ),
                    spec=input_group_by_metric_instance.spec,
                    defined_from=input_group_by_metric_instance.defined_from,
                )
            )
        return InstanceSet(
            measure_instances=tuple(output_measure_instances),
            dimension_instances=tuple(output_dimension_instances),
            time_dimension_instances=tuple(output_time_dimension_instances),
            entity_instances=tuple(output_entity_instances),
            group_by_metric_instances=tuple(output_group_by_metric_instances),
            metric_instances=tuple(output_metric_instances),
            metadata_instances=tuple(output_metadata_instances),
        )


class ConvertToMetadata(InstanceSetTransform[InstanceSet]):
    """Removes all instances from old instance set and replaces them with a set of metadata instances."""

    def __init__(self, metadata_instances: Sequence[MetadataInstance]) -> None:  # noqa: D107
        self._metadata_instances = metadata_instances

    def transform(self, instance_set: InstanceSet) -> InstanceSet:  # noqa: D102
        return InstanceSet(
            metadata_instances=tuple(self._metadata_instances),
        )


def create_select_columns_for_instance_sets(
    column_resolver: ColumnAssociationResolver,
    table_alias_to_instance_set: OrderedDict[str, InstanceSet],
) -> Tuple[SqlSelectColumn, ...]:
    """Creates select columns for instance sets coming from multiple table as defined in table_alias_to_instance_set.

    Used in cases where you join multiple tables and need to render select columns to access all of those.
    """
    column_set = SelectColumnSet()
    for table_alias, instance_set in table_alias_to_instance_set.items():
        column_set = column_set.merge(
            instance_set.transform(
                CreateSelectColumnsForInstances(
                    table_alias=table_alias,
                    column_resolver=column_resolver,
                )
            )
        )

    return column_set.as_tuple()


class AddMetadata(InstanceSetTransform[InstanceSet]):
    """Adds the given metric instances to the instance set."""

    def __init__(self, metadata_instances: Sequence[MetadataInstance]) -> None:  # noqa: D107
        self._metadata_instances = metadata_instances

    def transform(self, instance_set: InstanceSet) -> InstanceSet:  # noqa: D102
        return InstanceSet(
            measure_instances=instance_set.measure_instances,
            dimension_instances=instance_set.dimension_instances,
            time_dimension_instances=instance_set.time_dimension_instances,
            entity_instances=instance_set.entity_instances,
            group_by_metric_instances=instance_set.group_by_metric_instances,
            metric_instances=instance_set.metric_instances,
            metadata_instances=instance_set.metadata_instances + tuple(self._metadata_instances),
        )
