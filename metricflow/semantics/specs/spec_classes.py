"""These spec classes generally describe something that we want or have.

* Not too many comments here since they seem mostly self-explanatory.
* To see whether a spec matches something that already exists, there could be a method that allows you to match a spec
  to another spec or relevant object.
* The match() method will enable sub-classes (may require some restructuring) to use specs to request things like,
  metrics named "sales*".

TODO: Split this file into separate files.
"""

from __future__ import annotations

import itertools
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from hashlib import sha1
from typing import TYPE_CHECKING, Any, Dict, Generic, List, Optional, Sequence, Tuple, TypeVar, Union

from dbt_semantic_interfaces.dataclass_serialization import SerializableDataclass
from dbt_semantic_interfaces.implementations.metric import PydanticMetricTimeWindow
from dbt_semantic_interfaces.naming.keywords import DUNDER, METRIC_TIME_ELEMENT_NAME
from dbt_semantic_interfaces.protocols import MetricTimeWindow, WhereFilterIntersection
from dbt_semantic_interfaces.references import (
    DimensionReference,
    EntityReference,
    MeasureReference,
    MetricReference,
    TimeDimensionReference,
)
from dbt_semantic_interfaces.type_enums.aggregation_type import AggregationType
from dbt_semantic_interfaces.type_enums.date_part import DatePart
from dbt_semantic_interfaces.type_enums.time_granularity import TimeGranularity
from typing_extensions import override

from metricflow.semantics.aggregation_properties import AggregationState
from metricflow.semantics.collection_helpers.merger import Mergeable
from metricflow.semantics.filters.time_constraint import TimeRangeConstraint
from metricflow.semantics.naming.linkable_spec_name import StructuredLinkableSpecName
from metricflow.semantics.query.group_by_item.filter_spec_resolution.filter_spec_lookup import (
    FilterSpecResolutionLookUp,
)
from metricflow.semantics.sql.sql_bind_parameters import SqlBindParameters
from metricflow.semantics.sql.sql_column_type import SqlColumnType
from metricflow.semantics.sql.sql_join_type import SqlJoinType
from metricflow.semantics.visitor import VisitorOutputT

if TYPE_CHECKING:
    from metricflow.semantics.model.semantics.metric_lookup import MetricLookup
    from metricflow.semantics.model.semantics.semantic_model_lookup import SemanticModelLookup


def hash_items(items: Sequence[SqlColumnType]) -> str:
    """Produces a hash from a list of strings."""
    hash_builder = sha1()
    for item in items:
        hash_builder.update(str(item).encode("utf-8"))
    return hash_builder.hexdigest()


class InstanceSpecVisitor(Generic[VisitorOutputT], ABC):
    """Visitor for the InstanceSpec classes."""

    @abstractmethod
    def visit_measure_spec(self, measure_spec: MeasureSpec) -> VisitorOutputT:  # noqa: D102
        raise NotImplementedError

    @abstractmethod
    def visit_dimension_spec(self, dimension_spec: DimensionSpec) -> VisitorOutputT:  # noqa: D102
        raise NotImplementedError

    @abstractmethod
    def visit_time_dimension_spec(self, time_dimension_spec: TimeDimensionSpec) -> VisitorOutputT:  # noqa: D102
        raise NotImplementedError

    @abstractmethod
    def visit_entity_spec(self, entity_spec: EntitySpec) -> VisitorOutputT:  # noqa: D102
        raise NotImplementedError

    @abstractmethod
    def visit_group_by_metric_spec(self, group_by_metric_spec: GroupByMetricSpec) -> VisitorOutputT:  # noqa: D102
        raise NotImplementedError

    @abstractmethod
    def visit_metric_spec(self, metric_spec: MetricSpec) -> VisitorOutputT:  # noqa: D102
        raise NotImplementedError

    @abstractmethod
    def visit_metadata_spec(self, metadata_spec: MetadataSpec) -> VisitorOutputT:  # noqa: D102
        raise NotImplementedError


@dataclass(frozen=True)
class InstanceSpec(SerializableDataclass):
    """A specification for an instance of a metric definition object.

    An instance is different from the definition object in that it correlates to columns in the data flow and can be in
    different states. e.g. a time dimension at a different time granularity.

    This can't be a Protocol as base classes of Protocols need to be Protocols.
    """

    """Name of the dimension or entity in the semantic model."""
    element_name: str

    @staticmethod
    def merge(*specs: Sequence[InstanceSpec]) -> List[InstanceSpec]:
        """Merge all specs into a single list."""
        result: List[InstanceSpec] = []
        for spec in specs:
            result.extend(spec)
        return result

    @property
    def qualified_name(self) -> str:
        """Return the qualified name of this spec. e.g. "user_id__country"."""
        raise NotImplementedError()

    def accept(self, visitor: InstanceSpecVisitor[VisitorOutputT]) -> VisitorOutputT:
        """See Visitable."""
        raise NotImplementedError()

    @property
    @abstractmethod
    def as_spec_set(self) -> InstanceSpecSet:
        """Return this as the one item in a InstanceSpecSet."""
        raise NotImplementedError


SelfTypeT = TypeVar("SelfTypeT", bound="LinkableInstanceSpec")


@dataclass(frozen=True)
class MetadataSpec(InstanceSpec):
    """A specification for a specification that is built during the dataflow plan and not defined in config."""

    element_name: str
    agg_type: Optional[AggregationType] = None

    @property
    def qualified_name(self) -> str:  # noqa: D102
        return f"{self.element_name}{DUNDER}{self.agg_type.value}" if self.agg_type else self.element_name

    @staticmethod
    def from_name(name: str, agg_type: Optional[AggregationType] = None) -> MetadataSpec:  # noqa: D102
        return MetadataSpec(element_name=name, agg_type=agg_type)

    def accept(self, visitor: InstanceSpecVisitor[VisitorOutputT]) -> VisitorOutputT:  # noqa: D102
        return visitor.visit_metadata_spec(self)

    @property
    @override
    def as_spec_set(self) -> InstanceSpecSet:
        return InstanceSpecSet(metadata_specs=(self,))


@dataclass(frozen=True)
class LinkableInstanceSpec(InstanceSpec, ABC):
    """Generally a dimension or entity that may be specified using entity links.

    For example, user_id__country -> LinkableElementSpec(element_name="country", entity_links=["user_id"]

    See InstanceSpec for the reason behind "type: ignore"
    """

    """A list representing the join path of entities to get to this element."""
    entity_links: Tuple[EntityReference, ...]

    @property
    def without_first_entity_link(self: SelfTypeT) -> SelfTypeT:
        """e.g. user_id__device_id__platform -> device_id__platform."""
        raise NotImplementedError()

    @property
    def without_entity_links(self: SelfTypeT) -> SelfTypeT:
        """e.g. user_id__device_id__platform -> platform."""
        raise NotImplementedError()

    @staticmethod
    def merge_linkable_specs(*specs: Sequence[LinkableInstanceSpec]) -> List[LinkableInstanceSpec]:
        """Merge all specs into a single list."""
        result: List[LinkableInstanceSpec] = []
        for spec in specs:
            result.extend(spec)
        return result

    @property
    def qualified_name(self) -> str:
        """Return the qualified name of this spec. e.g. "user_id__country"."""
        return StructuredLinkableSpecName(
            entity_link_names=tuple(x.element_name for x in self.entity_links), element_name=self.element_name
        ).qualified_name


@dataclass(frozen=True)
class EntitySpec(LinkableInstanceSpec, SerializableDataclass):  # noqa: D101
    @property
    def without_first_entity_link(self) -> EntitySpec:  # noqa: D102
        assert len(self.entity_links) > 0, f"Spec does not have any entity links: {self}"
        return EntitySpec(element_name=self.element_name, entity_links=self.entity_links[1:])

    @property
    def without_entity_links(self) -> EntitySpec:  # noqa: D102
        return LinklessEntitySpec.from_element_name(self.element_name)

    @property
    def as_linkless_prefix(self) -> Tuple[EntityReference, ...]:
        """Creates tuple of linkless entities that could be included in the entity_links of another spec.

        eg as a prefix to a DimensionSpec's entity links to when a join is occurring via this entity
        """
        return (EntityReference(element_name=self.element_name),) + self.entity_links

    @staticmethod
    def from_name(name: str) -> EntitySpec:  # noqa: D102
        structured_name = StructuredLinkableSpecName.from_name(name)
        return EntitySpec(
            entity_links=tuple(EntityReference(idl) for idl in structured_name.entity_link_names),
            element_name=structured_name.element_name,
        )

    def __eq__(self, other: Any) -> bool:  # type: ignore[misc]  # noqa: D105
        if not isinstance(other, EntitySpec):
            return False
        return self.element_name == other.element_name and self.entity_links == other.entity_links

    def __hash__(self) -> int:  # noqa: D105
        return hash((self.element_name, self.entity_links))

    @property
    def reference(self) -> EntityReference:  # noqa: D102
        return EntityReference(element_name=self.element_name)

    @property
    @override
    def as_spec_set(self) -> InstanceSpecSet:
        return InstanceSpecSet(entity_specs=(self,))

    def accept(self, visitor: InstanceSpecVisitor[VisitorOutputT]) -> VisitorOutputT:  # noqa: D102
        return visitor.visit_entity_spec(self)


@dataclass(frozen=True)
class GroupByMetricSpec(LinkableInstanceSpec, SerializableDataclass):
    """Metric used in group by or where filter."""

    @property
    def without_first_entity_link(self) -> GroupByMetricSpec:  # noqa: D102
        assert len(self.entity_links) > 0, f"Spec does not have any entity links: {self}"
        return GroupByMetricSpec(element_name=self.element_name, entity_links=self.entity_links[1:])

    @property
    def without_entity_links(self) -> GroupByMetricSpec:  # noqa: D102
        return GroupByMetricSpec(element_name=self.element_name, entity_links=())

    @staticmethod
    def from_name(name: str) -> GroupByMetricSpec:  # noqa: D102
        structured_name = StructuredLinkableSpecName.from_name(name)
        return GroupByMetricSpec(
            entity_links=tuple(EntityReference(idl) for idl in structured_name.entity_link_names),
            element_name=structured_name.element_name,
        )

    def __eq__(self, other: Any) -> bool:  # type: ignore[misc] # noqa: D105
        if not isinstance(other, GroupByMetricSpec):
            return False
        return self.element_name == other.element_name and self.entity_links == other.entity_links

    def __hash__(self) -> int:  # noqa: D105
        return hash((self.element_name, self.entity_links))

    @property
    def reference(self) -> MetricReference:  # noqa: D102
        return MetricReference(element_name=self.element_name)

    @property
    @override
    def as_spec_set(self) -> InstanceSpecSet:
        return InstanceSpecSet(group_by_metric_specs=(self,))

    def accept(self, visitor: InstanceSpecVisitor[VisitorOutputT]) -> VisitorOutputT:  # noqa: D102
        return visitor.visit_group_by_metric_spec(self)

    @property
    def query_spec_for_source_node(self) -> MetricFlowQuerySpec:
        """Query spec that can be used to build a source node for this spec in the DFP."""
        return MetricFlowQuerySpec(
            metric_specs=(MetricSpec(element_name=self.element_name),),
            entity_specs=tuple(EntitySpec.from_name(entity_link.element_name) for entity_link in self.entity_links),
        )


@dataclass(frozen=True)
class LinklessEntitySpec(EntitySpec, SerializableDataclass):
    """Similar to EntitySpec, but requires that it doesn't have entity links."""

    @staticmethod
    def from_element_name(element_name: str) -> LinklessEntitySpec:  # noqa: D102
        return LinklessEntitySpec(element_name=element_name, entity_links=())

    def __post_init__(self) -> None:  # noqa: D105
        if len(self.entity_links) > 0:
            raise RuntimeError(f"{self.__class__.__name__} shouldn't have entity links. Got: {self}")

    def __eq__(self, other: Any) -> bool:  # type: ignore[misc]  # noqa: D105
        if not isinstance(other, EntitySpec):
            return False
        return self.element_name == other.element_name and self.entity_links == other.entity_links

    def __hash__(self) -> int:  # noqa: D105
        return hash((self.element_name, self.entity_links))

    @staticmethod
    def from_reference(entity_reference: EntityReference) -> LinklessEntitySpec:  # noqa: D102
        return LinklessEntitySpec(element_name=entity_reference.element_name, entity_links=())


@dataclass(frozen=True)
class DimensionSpec(LinkableInstanceSpec, SerializableDataclass):  # noqa: D101
    element_name: str
    entity_links: Tuple[EntityReference, ...]

    @property
    def without_first_entity_link(self) -> DimensionSpec:  # noqa: D102
        assert len(self.entity_links) > 0, f"Spec does not have any entity links: {self}"
        return DimensionSpec(element_name=self.element_name, entity_links=self.entity_links[1:])

    @property
    def without_entity_links(self) -> DimensionSpec:  # noqa: D102
        return DimensionSpec(element_name=self.element_name, entity_links=())

    @staticmethod
    def from_linkable(spec: LinkableInstanceSpec) -> DimensionSpec:  # noqa: D102
        return DimensionSpec(element_name=spec.element_name, entity_links=spec.entity_links)

    @staticmethod
    def from_name(name: str) -> DimensionSpec:
        """Construct from a name e.g. listing__ds__month."""
        parsed_name = StructuredLinkableSpecName.from_name(name)
        return DimensionSpec(
            entity_links=tuple([EntityReference(idl) for idl in parsed_name.entity_link_names]),
            element_name=parsed_name.element_name,
        )

    @property
    def reference(self) -> DimensionReference:  # noqa: D102
        return DimensionReference(element_name=self.element_name)

    @property
    @override
    def as_spec_set(self) -> InstanceSpecSet:
        return InstanceSpecSet(dimension_specs=(self,))

    def accept(self, visitor: InstanceSpecVisitor[VisitorOutputT]) -> VisitorOutputT:  # noqa: D102
        return visitor.visit_dimension_spec(self)


class TimeDimensionSpecField(Enum):
    """Fields of the time dimension spec.

    The value corresponds to the name of the field in the dataclass. This should contain all fields, but implementation
    is pending.
    """

    TIME_GRANULARITY = "time_granularity"


class TimeDimensionSpecComparisonKey:
    """A key that can be used for comparing / grouping time dimension specs.

    Useful for assessing if two time dimension specs are equal while ignoring specific attributes. Keys must have the
    same set of excluded attributes to be valid for comparison.

    This is useful for ambiguous group-by-item resolution where we want to select a time dimension regardless of the
    grain.
    """

    def __init__(self, source_spec: TimeDimensionSpec, exclude_fields: Sequence[TimeDimensionSpecField]) -> None:
        """Initializer.

        Args:
            source_spec: The spec that this key is based on.
            exclude_fields: The fields to ignore when determining equality.
        """
        self._excluded_fields = frozenset(exclude_fields)
        self._source_spec = source_spec

        # This is a list of field values of TimeDimensionSpec that we should use for comparison.
        spec_field_values_for_comparison: List[
            Union[str, Tuple[EntityReference, ...], TimeGranularity, Optional[DatePart]]
        ] = [self._source_spec.element_name, self._source_spec.entity_links]

        if TimeDimensionSpecField.TIME_GRANULARITY not in self._excluded_fields:
            spec_field_values_for_comparison.append(self._source_spec.time_granularity)

        spec_field_values_for_comparison.append(self._source_spec.date_part)

        self._spec_field_values_for_comparison = tuple(spec_field_values_for_comparison)

    @property
    def source_spec(self) -> TimeDimensionSpec:  # noqa: D102
        return self._source_spec

    @override
    def __eq__(self, other: Any) -> bool:  # type: ignore[misc]
        if not isinstance(other, TimeDimensionSpecComparisonKey):
            return False

        if self._excluded_fields != other._excluded_fields:
            return False

        return self._spec_field_values_for_comparison == other._spec_field_values_for_comparison

    @override
    def __hash__(self) -> int:
        return hash((self._excluded_fields, self._spec_field_values_for_comparison))


DEFAULT_TIME_GRANULARITY = TimeGranularity.DAY


@dataclass(frozen=True)
class TimeDimensionSpec(DimensionSpec):  # noqa: D101
    time_granularity: TimeGranularity = DEFAULT_TIME_GRANULARITY
    date_part: Optional[DatePart] = None

    # Used for semi-additive joins. Some more thought is needed, but this may be useful in InstanceSpec.
    aggregation_state: Optional[AggregationState] = None

    @property
    def without_first_entity_link(self) -> TimeDimensionSpec:  # noqa: D102
        assert len(self.entity_links) > 0, f"Spec does not have any entity links: {self}"
        return TimeDimensionSpec(
            element_name=self.element_name,
            entity_links=self.entity_links[1:],
            time_granularity=self.time_granularity,
            date_part=self.date_part,
        )

    @property
    def without_entity_links(self) -> TimeDimensionSpec:  # noqa: D102
        return TimeDimensionSpec.from_name(self.element_name)

    @staticmethod
    def from_name(name: str) -> TimeDimensionSpec:  # noqa: D102
        structured_name = StructuredLinkableSpecName.from_name(name)
        return TimeDimensionSpec(
            entity_links=tuple(EntityReference(idl) for idl in structured_name.entity_link_names),
            element_name=structured_name.element_name,
            time_granularity=structured_name.time_granularity or DEFAULT_TIME_GRANULARITY,
        )

    @property
    def reference(self) -> TimeDimensionReference:  # noqa: D102
        return TimeDimensionReference(element_name=self.element_name)

    @property
    def dimension_reference(self) -> DimensionReference:  # noqa: D102
        return DimensionReference(element_name=self.element_name)

    @property
    def qualified_name(self) -> str:  # noqa: D102
        return StructuredLinkableSpecName(
            entity_link_names=tuple(x.element_name for x in self.entity_links),
            element_name=self.element_name,
            time_granularity=self.time_granularity,
            date_part=self.date_part,
        ).qualified_name

    @staticmethod
    def from_reference(reference: TimeDimensionReference) -> TimeDimensionSpec:
        """Initialize from a time dimension reference instance."""
        return TimeDimensionSpec(entity_links=(), element_name=reference.element_name)

    @property
    @override
    def as_spec_set(self) -> InstanceSpecSet:
        return InstanceSpecSet(time_dimension_specs=(self,))

    def accept(self, visitor: InstanceSpecVisitor[VisitorOutputT]) -> VisitorOutputT:  # noqa: D102
        return visitor.visit_time_dimension_spec(self)

    def with_grain(self, time_granularity: TimeGranularity) -> TimeDimensionSpec:  # noqa: D102
        return TimeDimensionSpec(
            element_name=self.element_name,
            entity_links=self.entity_links,
            time_granularity=time_granularity,
            date_part=self.date_part,
            aggregation_state=self.aggregation_state,
        )

    def with_aggregation_state(self, aggregation_state: AggregationState) -> TimeDimensionSpec:  # noqa: D102
        return TimeDimensionSpec(
            element_name=self.element_name,
            entity_links=self.entity_links,
            time_granularity=self.time_granularity,
            date_part=self.date_part,
            aggregation_state=aggregation_state,
        )

    def comparison_key(self, exclude_fields: Sequence[TimeDimensionSpecField] = ()) -> TimeDimensionSpecComparisonKey:
        """See TimeDimensionComparisonKey."""
        return TimeDimensionSpecComparisonKey(
            source_spec=self,
            exclude_fields=exclude_fields,
        )

    @staticmethod
    def generate_possible_specs_for_time_dimension(
        time_dimension_reference: TimeDimensionReference, entity_links: Tuple[EntityReference, ...]
    ) -> List[TimeDimensionSpec]:
        """Generate a list of time dimension specs with all combinations of granularity & date part."""
        time_dimension_specs: List[TimeDimensionSpec] = []
        for time_granularity in TimeGranularity:
            time_dimension_specs.append(
                TimeDimensionSpec(
                    element_name=time_dimension_reference.element_name,
                    entity_links=entity_links,
                    time_granularity=time_granularity,
                    date_part=None,
                )
            )
        for date_part in DatePart:
            for time_granularity in date_part.compatible_granularities:
                time_dimension_specs.append(
                    TimeDimensionSpec(
                        element_name=time_dimension_reference.element_name,
                        entity_links=entity_links,
                        time_granularity=time_granularity,
                        date_part=date_part,
                    )
                )
        return time_dimension_specs

    @property
    def is_metric_time(self) -> bool:  # noqa: D102
        return self.element_name == METRIC_TIME_ELEMENT_NAME


@dataclass(frozen=True)
class NonAdditiveDimensionSpec(SerializableDataclass):
    """Spec representing non-additive dimension parameters for use within a MeasureSpec.

    This is sourced from the NonAdditiveDimensionParameters model object, which provides the parsed parameter set,
    while the spec contains the information needed for dataflow plan operations
    """

    name: str
    window_choice: AggregationType
    window_groupings: Tuple[str, ...] = ()

    @property
    def bucket_hash(self) -> str:
        """Returns the hash value used for grouping equivalent params."""
        values = [self.window_choice.name, self.name]
        values.extend(sorted(self.window_groupings))
        return hash_items(values)

    @property
    def linkable_specs(self) -> LinkableSpecSet:  # noqa: D102
        return LinkableSpecSet(
            dimension_specs=(),
            time_dimension_specs=(TimeDimensionSpec.from_name(self.name),),
            entity_specs=tuple(
                LinklessEntitySpec.from_element_name(entity_name) for entity_name in self.window_groupings
            ),
        )

    def __eq__(self, other: Any) -> bool:  # type: ignore[misc]  # noqa: D105
        if not isinstance(other, NonAdditiveDimensionSpec):
            return False
        return self.bucket_hash == other.bucket_hash


@dataclass(frozen=True)
class MeasureSpec(InstanceSpec):  # noqa: D101
    element_name: str
    non_additive_dimension_spec: Optional[NonAdditiveDimensionSpec] = None
    fill_nulls_with: Optional[int] = None

    @staticmethod
    def from_name(name: str) -> MeasureSpec:
        """Construct from a name e.g. listing__ds__month."""
        return MeasureSpec(element_name=name)

    @staticmethod
    def from_reference(reference: MeasureReference) -> MeasureSpec:
        """Initialize from a measure reference instance."""
        return MeasureSpec(element_name=reference.element_name)

    @property
    def qualified_name(self) -> str:  # noqa: D102
        return self.element_name

    @property
    def reference(self) -> MeasureReference:  # noqa: D102
        return MeasureReference(element_name=self.element_name)

    def accept(self, visitor: InstanceSpecVisitor[VisitorOutputT]) -> VisitorOutputT:  # noqa: D102
        return visitor.visit_measure_spec(self)

    @property
    @override
    def as_spec_set(self) -> InstanceSpecSet:
        return InstanceSpecSet(measure_specs=(self,))


@dataclass(frozen=True)
class MetricSpec(InstanceSpec):  # noqa: D101
    # Time-over-time could go here
    element_name: str
    filter_specs: Tuple[WhereFilterSpec, ...] = ()
    alias: Optional[str] = None
    offset_window: Optional[PydanticMetricTimeWindow] = None
    offset_to_grain: Optional[TimeGranularity] = None

    @staticmethod
    def from_element_name(element_name: str) -> MetricSpec:  # noqa: D102
        return MetricSpec(element_name=element_name)

    @property
    def qualified_name(self) -> str:  # noqa: D102
        return self.element_name

    @staticmethod
    def from_reference(reference: MetricReference) -> MetricSpec:
        """Initialize from a metric reference instance."""
        return MetricSpec(element_name=reference.element_name)

    def accept(self, visitor: InstanceSpecVisitor[VisitorOutputT]) -> VisitorOutputT:  # noqa: D102
        return visitor.visit_metric_spec(self)

    @property
    @override
    def as_spec_set(self) -> InstanceSpecSet:
        return InstanceSpecSet(metric_specs=(self,))

    @property
    def reference(self) -> MetricReference:
        """Return the reference object that is used for referencing the associated metric in the manifest."""
        return MetricReference(element_name=self.element_name)

    @property
    def has_time_offset(self) -> bool:  # noqa: D102
        return bool(self.offset_window or self.offset_to_grain)

    def without_offset(self) -> MetricSpec:
        """Represents the metric spec with any time offsets removed."""
        return MetricSpec(element_name=self.element_name, filter_specs=self.filter_specs, alias=self.alias)


@dataclass(frozen=True)
class CumulativeMeasureDescription:
    """If a measure is a part of a cumulative metric, this represents the associated parameters."""

    cumulative_window: Optional[MetricTimeWindow]
    cumulative_grain_to_date: Optional[TimeGranularity]


@dataclass(frozen=True)
class MetricInputMeasureSpec(SerializableDataclass):
    """The spec for a measure defined as a base metric input.

    This is necessary because the MeasureSpec is used as a key linking the measures used in the query
    to the measures defined in the semantic models. Adding metric-specific information, like constraints,
    causes lookups connecting query -> semantic model to fail in strange ways. This spec, then, provides
    both the key (in the form of a MeasureSpec) along with whatever measure-specific attributes
    a user might specify in a metric definition or query accessing the metric itself.
    """

    measure_spec: MeasureSpec
    fill_nulls_with: Optional[int] = None
    offset_window: Optional[MetricTimeWindow] = None
    offset_to_grain: Optional[TimeGranularity] = None
    cumulative_description: Optional[CumulativeMeasureDescription] = None
    filter_specs: Tuple[WhereFilterSpec, ...] = ()
    alias: Optional[str] = None
    before_aggregation_time_spine_join_description: Optional[JoinToTimeSpineDescription] = None
    after_aggregation_time_spine_join_description: Optional[JoinToTimeSpineDescription] = None

    @property
    def post_aggregation_spec(self) -> MeasureSpec:
        """Return a MeasureSpec instance representing the post-aggregation spec state for the underlying measure."""
        if self.alias:
            return MeasureSpec(
                element_name=self.alias,
                non_additive_dimension_spec=self.measure_spec.non_additive_dimension_spec,
                fill_nulls_with=self.fill_nulls_with,
            )
        else:
            return self.measure_spec


@dataclass(frozen=True)
class OrderBySpec(SerializableDataclass):  # noqa: D101
    instance_spec: InstanceSpec
    descending: bool


@dataclass(frozen=True)
class FilterSpec(SerializableDataclass):  # noqa: D101
    expr: str
    elements: Tuple[InstanceSpec, ...]


@dataclass(frozen=True)
class LinkableSpecSet(Mergeable, SerializableDataclass):
    """Groups linkable specs."""

    dimension_specs: Tuple[DimensionSpec, ...] = ()
    time_dimension_specs: Tuple[TimeDimensionSpec, ...] = ()
    entity_specs: Tuple[EntitySpec, ...] = ()
    group_by_metric_specs: Tuple[GroupByMetricSpec, ...] = ()

    @property
    def contains_metric_time(self) -> bool:
        """Returns true if this set contains a spec referring to metric time at any grain."""
        return len(self.metric_time_specs) > 0

    def included_agg_time_dimension_specs_for_metric(
        self, metric_reference: MetricReference, metric_lookup: MetricLookup
    ) -> List[TimeDimensionSpec]:
        """Get the time dims included that are valid agg time dimensions for the specified metric."""
        queried_metric_time_specs = list(self.metric_time_specs)

        valid_agg_time_dimensions = metric_lookup.get_valid_agg_time_dimensions_for_metric(metric_reference)
        queried_agg_time_dimension_specs = (
            list(set(self.time_dimension_specs).intersection(set(valid_agg_time_dimensions)))
            + queried_metric_time_specs
        )

        return queried_agg_time_dimension_specs

    def included_agg_time_dimension_specs_for_measure(
        self, measure_reference: MeasureReference, semantic_model_lookup: SemanticModelLookup
    ) -> List[TimeDimensionSpec]:
        """Get the time dims included that are valid agg time dimensions for the specified measure."""
        queried_metric_time_specs = list(self.metric_time_specs)

        valid_agg_time_dimensions = semantic_model_lookup.get_agg_time_dimension_specs_for_measure(measure_reference)
        queried_agg_time_dimension_specs = (
            list(set(self.time_dimension_specs).intersection(set(valid_agg_time_dimensions)))
            + queried_metric_time_specs
        )

        return queried_agg_time_dimension_specs

    @property
    def metric_time_specs(self) -> Sequence[TimeDimensionSpec]:
        """Returns any specs referring to metric time at any grain."""
        return tuple(
            time_dimension_spec
            for time_dimension_spec in self.time_dimension_specs
            if time_dimension_spec.is_metric_time
        )

    @property
    def as_tuple(self) -> Tuple[LinkableInstanceSpec, ...]:  # noqa: D102
        return tuple(
            itertools.chain(
                self.dimension_specs, self.time_dimension_specs, self.entity_specs, self.group_by_metric_specs
            )
        )

    @override
    def merge(self, other: LinkableSpecSet) -> LinkableSpecSet:
        return LinkableSpecSet(
            dimension_specs=self.dimension_specs + other.dimension_specs,
            time_dimension_specs=self.time_dimension_specs + other.time_dimension_specs,
            entity_specs=self.entity_specs + other.entity_specs,
            group_by_metric_specs=self.group_by_metric_specs + other.group_by_metric_specs,
        )

    @override
    @classmethod
    def empty_instance(cls) -> LinkableSpecSet:
        return LinkableSpecSet()

    def dedupe(self) -> LinkableSpecSet:  # noqa: D102
        # Use dictionaries to dedupe as it preserves insertion order.

        dimension_spec_dict: Dict[DimensionSpec, None] = {}
        for dimension_spec in self.dimension_specs:
            dimension_spec_dict[dimension_spec] = None

        time_dimension_spec_dict: Dict[TimeDimensionSpec, None] = {}
        for time_dimension_spec in self.time_dimension_specs:
            time_dimension_spec_dict[time_dimension_spec] = None

        entity_spec_dict: Dict[EntitySpec, None] = {}
        for entity_spec in self.entity_specs:
            entity_spec_dict[entity_spec] = None

        group_by_metric_spec_dict: Dict[GroupByMetricSpec, None] = {}
        for group_by_metric in self.group_by_metric_specs:
            group_by_metric_spec_dict[group_by_metric] = None

        return LinkableSpecSet(
            dimension_specs=tuple(dimension_spec_dict.keys()),
            time_dimension_specs=tuple(time_dimension_spec_dict.keys()),
            entity_specs=tuple(entity_spec_dict.keys()),
            group_by_metric_specs=tuple(group_by_metric_spec_dict.keys()),
        )

    def is_subset_of(self, other_set: LinkableSpecSet) -> bool:  # noqa: D102
        return set(self.as_tuple).issubset(set(other_set.as_tuple))

    @property
    def as_spec_set(self) -> InstanceSpecSet:  # noqa: D102
        return InstanceSpecSet(
            dimension_specs=self.dimension_specs,
            time_dimension_specs=self.time_dimension_specs,
            entity_specs=self.entity_specs,
            group_by_metric_specs=self.group_by_metric_specs,
        )

    def difference(self, other: LinkableSpecSet) -> LinkableSpecSet:  # noqa: D102
        return LinkableSpecSet(
            dimension_specs=tuple(set(self.dimension_specs) - set(other.dimension_specs)),
            time_dimension_specs=tuple(set(self.time_dimension_specs) - set(other.time_dimension_specs)),
            entity_specs=tuple(set(self.entity_specs) - set(other.entity_specs)),
            group_by_metric_specs=tuple(set(self.group_by_metric_specs) - set(other.group_by_metric_specs)),
        )

    def __len__(self) -> int:  # noqa: D105
        return len(self.dimension_specs) + len(self.time_dimension_specs) + len(self.entity_specs)

    @staticmethod
    def from_specs(specs: Sequence[LinkableInstanceSpec]) -> LinkableSpecSet:  # noqa: D102
        instance_spec_set = InstanceSpecSet.from_specs(specs)
        return LinkableSpecSet(
            dimension_specs=instance_spec_set.dimension_specs,
            time_dimension_specs=instance_spec_set.time_dimension_specs,
            entity_specs=instance_spec_set.entity_specs,
            group_by_metric_specs=instance_spec_set.group_by_metric_specs,
        )


@dataclass(frozen=True)
class MetricFlowQuerySpec(SerializableDataclass):
    """Specs needed for running a query."""

    metric_specs: Tuple[MetricSpec, ...] = ()
    dimension_specs: Tuple[DimensionSpec, ...] = ()
    entity_specs: Tuple[EntitySpec, ...] = ()
    time_dimension_specs: Tuple[TimeDimensionSpec, ...] = ()
    group_by_metric_specs: Tuple[GroupByMetricSpec, ...] = ()
    order_by_specs: Tuple[OrderBySpec, ...] = ()
    time_range_constraint: Optional[TimeRangeConstraint] = None
    limit: Optional[int] = None
    filter_intersection: Optional[WhereFilterIntersection] = None
    filter_spec_resolution_lookup: FilterSpecResolutionLookUp = FilterSpecResolutionLookUp.empty_instance()
    min_max_only: bool = False

    @property
    def linkable_specs(self) -> LinkableSpecSet:  # noqa: D102
        return LinkableSpecSet(
            dimension_specs=self.dimension_specs,
            time_dimension_specs=self.time_dimension_specs,
            entity_specs=self.entity_specs,
            group_by_metric_specs=self.group_by_metric_specs,
        )

    def with_time_range_constraint(self, time_range_constraint: Optional[TimeRangeConstraint]) -> MetricFlowQuerySpec:
        """Return a query spec that's the same as self but with a different time_range_constraint."""
        return MetricFlowQuerySpec(
            metric_specs=self.metric_specs,
            dimension_specs=self.dimension_specs,
            entity_specs=self.entity_specs,
            time_dimension_specs=self.time_dimension_specs,
            group_by_metric_specs=self.group_by_metric_specs,
            order_by_specs=self.order_by_specs,
            time_range_constraint=time_range_constraint,
            limit=self.limit,
            filter_intersection=self.filter_intersection,
            filter_spec_resolution_lookup=self.filter_spec_resolution_lookup,
        )


TransformOutputT = TypeVar("TransformOutputT")


class InstanceSpecSetTransform(Generic[TransformOutputT], ABC):
    """Function to use for transforming spec sets."""

    @abstractmethod
    def transform(self, spec_set: InstanceSpecSet) -> TransformOutputT:  # noqa: D102
        pass


@dataclass(frozen=True)
class InstanceSpecSet(Mergeable, SerializableDataclass):
    """Consolidates all specs used in an instance set."""

    metric_specs: Tuple[MetricSpec, ...] = ()
    measure_specs: Tuple[MeasureSpec, ...] = ()
    dimension_specs: Tuple[DimensionSpec, ...] = ()
    entity_specs: Tuple[EntitySpec, ...] = ()
    time_dimension_specs: Tuple[TimeDimensionSpec, ...] = ()
    group_by_metric_specs: Tuple[GroupByMetricSpec, ...] = ()
    metadata_specs: Tuple[MetadataSpec, ...] = ()

    @override
    def merge(self, other: InstanceSpecSet) -> InstanceSpecSet:
        return InstanceSpecSet(
            metric_specs=self.metric_specs + other.metric_specs,
            measure_specs=self.measure_specs + other.measure_specs,
            dimension_specs=self.dimension_specs + other.dimension_specs,
            entity_specs=self.entity_specs + other.entity_specs,
            group_by_metric_specs=self.group_by_metric_specs + other.group_by_metric_specs,
            time_dimension_specs=self.time_dimension_specs + other.time_dimension_specs,
            metadata_specs=self.metadata_specs + other.metadata_specs,
        )

    @override
    @classmethod
    def empty_instance(cls) -> InstanceSpecSet:
        return InstanceSpecSet()

    def dedupe(self) -> InstanceSpecSet:
        """De-duplicates repeated elements.

        TBD: Have merge de-duplicate instead.
        """
        metric_specs_deduped = []
        for metric_spec in self.metric_specs:
            if metric_spec not in metric_specs_deduped:
                metric_specs_deduped.append(metric_spec)

        measure_specs_deduped = []
        for measure_spec in self.measure_specs:
            if measure_spec not in measure_specs_deduped:
                measure_specs_deduped.append(measure_spec)

        dimension_specs_deduped = []
        for dimension_spec in self.dimension_specs:
            if dimension_spec not in dimension_specs_deduped:
                dimension_specs_deduped.append(dimension_spec)

        time_dimension_specs_deduped = []
        for time_dimension_spec in self.time_dimension_specs:
            if time_dimension_spec not in time_dimension_specs_deduped:
                time_dimension_specs_deduped.append(time_dimension_spec)

        entity_specs_deduped = []
        for entity_spec in self.entity_specs:
            if entity_spec not in entity_specs_deduped:
                entity_specs_deduped.append(entity_spec)

        group_by_metric_specs_deduped = []
        for group_by_metric_spec in self.group_by_metric_specs:
            if group_by_metric_spec not in group_by_metric_specs_deduped:
                group_by_metric_specs_deduped.append(group_by_metric_spec)

        return InstanceSpecSet(
            metric_specs=tuple(metric_specs_deduped),
            measure_specs=tuple(measure_specs_deduped),
            dimension_specs=tuple(dimension_specs_deduped),
            time_dimension_specs=tuple(time_dimension_specs_deduped),
            entity_specs=tuple(entity_specs_deduped),
            group_by_metric_specs=tuple(group_by_metric_specs_deduped),
        )

    @property
    def linkable_specs(self) -> Sequence[LinkableInstanceSpec]:
        """All linkable specs in this set."""
        return list(
            itertools.chain(
                self.dimension_specs, self.time_dimension_specs, self.entity_specs, self.group_by_metric_specs
            )
        )

    @property
    def all_specs(self) -> Sequence[InstanceSpec]:  # noqa: D102
        return tuple(
            itertools.chain(
                self.measure_specs,
                self.dimension_specs,
                self.time_dimension_specs,
                self.entity_specs,
                self.group_by_metric_specs,
                self.metric_specs,
                self.metadata_specs,
            )
        )

    def transform(  # noqa: D102
        self, transform_function: InstanceSpecSetTransform[TransformOutputT]
    ) -> TransformOutputT:  # noqa: D102
        return transform_function.transform(self)

    @staticmethod
    def from_specs(specs: Sequence[InstanceSpec]) -> InstanceSpecSet:  # noqa: D102
        return InstanceSpecSet.merge_iterable(spec.as_spec_set for spec in specs)


@dataclass(frozen=True)
class PartitionSpecSet(SerializableDataclass):
    """Grouping of the linkable specs."""

    dimension_specs: Tuple[DimensionSpec, ...] = ()
    time_dimension_specs: Tuple[TimeDimensionSpec, ...] = ()


logger = logging.getLogger(__name__)


class WhereFilterResolutionException(Exception):  # noqa: D101
    pass


@dataclass(frozen=True)
class WhereFilterSpec(Mergeable, SerializableDataclass):
    """Similar to the WhereFilter, but with the where_sql_template rendered and used elements extracted.

    For example:

    WhereFilter(where_sql_template="{{ Dimension('listing__country') }} == 'US'"))

    ->

    ResolvedWhereFilter(
        where_sql="listing__country == 'US'",
        bind_parameters: SqlBindParameters(),
        linkable_spec_set: LinkableSpecSet(
            dimension_specs=(
                DimensionSpec(
                    element_name='country',
                    entity_links=('listing',),
            ),
        )
    )
    """

    # Debating whether where_sql / bind_parameters belongs here. where_sql may become dialect specific if we introduce
    # quoted identifiers later.
    where_sql: str
    bind_parameters: SqlBindParameters
    linkable_spec_set: LinkableSpecSet

    def merge(self, other: WhereFilterSpec) -> WhereFilterSpec:  # noqa: D102
        if self == WhereFilterSpec.empty_instance():
            return other

        if other == WhereFilterSpec.empty_instance():
            return self

        if self == other:
            return self

        return WhereFilterSpec(
            where_sql=f"({self.where_sql}) AND ({other.where_sql})",
            bind_parameters=self.bind_parameters.combine(other.bind_parameters),
            linkable_spec_set=self.linkable_spec_set.merge(other.linkable_spec_set).dedupe(),
        )

    @classmethod
    @override
    def empty_instance(cls) -> WhereFilterSpec:
        # Need to revisit making WhereFilterSpec Mergeable as it's current not a collection, and it's odd to return this
        # no-op filter. Use cases would need to check whether a WhereSpec is a no-op before rendering it to avoid an
        # un-necessary WHERE clause. Making WhereFilterSpec map to a WhereFilterIntersection would make this more in
        # line with other cases of Mergeable.
        return WhereFilterSpec(
            where_sql="TRUE",
            bind_parameters=SqlBindParameters(),
            linkable_spec_set=LinkableSpecSet(),
        )


@dataclass(frozen=True)
class ConstantPropertySpec(SerializableDataclass):
    """Includes the specs that are joined for conversion metric's constant properties."""

    base_spec: LinkableInstanceSpec
    conversion_spec: LinkableInstanceSpec


@dataclass(frozen=True)
class JoinToTimeSpineDescription:
    """Describes how a time spine join should be performed."""

    join_type: SqlJoinType
    offset_window: Optional[MetricTimeWindow]
    offset_to_grain: Optional[TimeGranularity]
