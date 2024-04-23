"""Module for testing linkable element set operations.

Note this module departs from our typical approach of defining a bajillion fixtures and wiring them into functions
because the base object types involved here are highly specific to the assertions we want to make in these tests.
Rather than making function calls, we simply initialize these things at module scope. This opens us up to possible
output divergence if someone updates one of these things by reference inside the LinkableElementSet, but since we
are not supposed to be doing that anyway that's actually a reasonably handy feature.
"""

from __future__ import annotations

import itertools

from dbt_semantic_interfaces.protocols.dimension import DimensionType
from dbt_semantic_interfaces.references import (
    DimensionReference,
    EntityReference,
    MetricReference,
    SemanticModelReference,
    TimeDimensionReference,
)
from dbt_semantic_interfaces.type_enums.time_granularity import TimeGranularity
from more_itertools import bucket

from metricflow.model.semantics.linkable_element import (
    LinkableDimension,
    LinkableElementProperty,
    LinkableEntity,
    LinkableMetric,
    SemanticModelJoinPathElement,
)
from metricflow.model.semantics.linkable_element_set import LinkableElementSet

AMBIGUOUS_NAME = "ambiguous"
# Common references
_base_semantic_model = SemanticModelReference(semantic_model_name="base_semantic_model")
_secondary_semantic_model = SemanticModelReference(semantic_model_name="secondary_semantic_model")
_base_entity_reference = EntityReference(element_name="base_entity")
_base_dimension_reference = DimensionReference(element_name="base_dimension")
_time_dimension_reference = TimeDimensionReference(element_name="time_dimension")
_base_metric_reference = MetricReference(element_name="base_metric")


# Entities
_base_entity = LinkableEntity(
    element_name=_base_entity_reference.element_name,
    semantic_model_origin=_base_semantic_model,
    entity_links=(),
    join_path=(),
    properties=frozenset([LinkableElementProperty.ENTITY]),
)
_ambiguous_entity = LinkableEntity(
    element_name=AMBIGUOUS_NAME,
    semantic_model_origin=_base_semantic_model,
    entity_links=(_base_entity_reference,),
    join_path=(),
    properties=frozenset([LinkableElementProperty.ENTITY, LinkableElementProperty.LOCAL_LINKED]),
)
# For testing deduplication on entities
_ambiguous_entity_with_join_path = LinkableEntity(
    element_name=AMBIGUOUS_NAME,
    semantic_model_origin=_base_semantic_model,
    entity_links=(_base_entity_reference,),
    join_path=(
        SemanticModelJoinPathElement(
            semantic_model_reference=_secondary_semantic_model,
            join_on_entity=EntityReference(element_name="external_entity"),
        ),
    ),
    properties=frozenset([LinkableElementProperty.ENTITY, LinkableElementProperty.JOINED]),
)

# Dimensions
_categorical_dimension = LinkableDimension(
    element_name=_base_dimension_reference.element_name,
    entity_links=(_base_entity_reference,),
    dimension_type=DimensionType.CATEGORICAL,
    semantic_model_origin=_base_semantic_model,
    join_path=(),
    properties=frozenset([LinkableElementProperty.LOCAL_LINKED]),
    time_granularity=None,
    date_part=None,
)
_time_dimension = LinkableDimension(
    element_name=_time_dimension_reference.element_name,
    entity_links=(_base_entity_reference,),
    dimension_type=DimensionType.TIME,
    semantic_model_origin=_base_semantic_model,
    join_path=(),
    properties=frozenset([LinkableElementProperty.LOCAL_LINKED]),
    time_granularity=TimeGranularity.DAY,
    date_part=None,
)
# Resolves to the same local linked name name as _ambiguous_entity
_ambiguous_categorical_dimension = LinkableDimension(
    element_name=AMBIGUOUS_NAME,
    entity_links=(_base_entity_reference,),
    dimension_type=DimensionType.CATEGORICAL,
    semantic_model_origin=_secondary_semantic_model,
    join_path=(),
    properties=frozenset([LinkableElementProperty.LOCAL_LINKED]),
    time_granularity=None,
    date_part=None,
)
# The opposite direction of the join tfor ambiguous_entity_with_join_path
# For testing deduplication on dimensions
_ambiguous_categorical_dimension_with_join_path = LinkableDimension(
    element_name=AMBIGUOUS_NAME,
    entity_links=(_base_entity_reference,),
    dimension_type=DimensionType.CATEGORICAL,
    semantic_model_origin=_secondary_semantic_model,
    join_path=(
        SemanticModelJoinPathElement(
            semantic_model_reference=_base_semantic_model, join_on_entity=_base_entity_reference
        ),
    ),
    properties=frozenset([LinkableElementProperty.JOINED]),
    time_granularity=None,
    date_part=None,
)

# Metrics
_base_metric = LinkableMetric(
    element_name=_base_metric_reference.element_name,
    join_by_semantic_model=_base_semantic_model,
    entity_links=(_base_entity_reference,),
    properties=frozenset([LinkableElementProperty.METRIC]),
    join_path=(),
)
_ambiguous_metric = LinkableMetric(
    element_name=AMBIGUOUS_NAME,
    join_by_semantic_model=_secondary_semantic_model,
    entity_links=(_base_entity_reference,),
    properties=frozenset([LinkableElementProperty.METRIC]),
    join_path=(),
)
# For testing deduplication on metrics
_ambiguous_metric_with_join_path = LinkableMetric(
    element_name=AMBIGUOUS_NAME,
    join_by_semantic_model=_secondary_semantic_model,
    entity_links=(_base_entity_reference,),
    properties=frozenset([LinkableElementProperty.METRIC, LinkableElementProperty.JOINED]),
    join_path=(
        SemanticModelJoinPathElement(
            semantic_model_reference=_base_semantic_model, join_on_entity=_base_entity_reference
        ),
    ),
)


def _linkable_set_with_uniques_and_duplicates() -> LinkableElementSet:
    """Helper to create a LinkableElementSet including unique and ambiguous items.

    The ambiguous elements will all resolve to the same ElementPathKey.

    For distinct items we'll see entries like:

        {_categorical_dimension.path_key: (_categorical_dimension,)}

    For ambiguous items we'll see entries like:

        {_ambiguous_entity.path_key: (_ambiguous_entity, _ambiguous_entity_with_join_path)}

    This also includes a cross-type ambiguity, where one dimension has the same name and entity link set as one of
    the entities. These will NOT resolve to the same ElementPathKey, because ElementPathKey incorporates elment type.
    """
    dimensions = bucket(
        (
            _categorical_dimension,
            _time_dimension,
            _ambiguous_categorical_dimension,
            _ambiguous_categorical_dimension_with_join_path,
        ),
        lambda x: x.path_key,
    )
    entities = bucket((_base_entity, _ambiguous_entity, _ambiguous_entity_with_join_path), lambda x: x.path_key)
    metrics = bucket((_base_metric, _ambiguous_metric, _ambiguous_metric_with_join_path), lambda x: x.path_key)

    return LinkableElementSet(
        path_key_to_linkable_dimensions={path_key: tuple(dimensions[path_key]) for path_key in list(dimensions)},
        path_key_to_linkable_entities={path_key: tuple(entities[path_key]) for path_key in list(entities)},
        path_key_to_linkable_metrics={path_key: tuple(metrics[path_key]) for path_key in list(metrics)},
    )


def test_filter_with_any_of() -> None:
    """Tests behavior of filter method with a `with_any_of` specified."""
    filter_properties = frozenset([LinkableElementProperty.JOINED, LinkableElementProperty.ENTITY])
    linkable_set = _linkable_set_with_uniques_and_duplicates()

    filtered_set = linkable_set.filter(with_any_of=filter_properties)

    filtered_dimensions = [
        dim for dim in itertools.chain.from_iterable(filtered_set.path_key_to_linkable_dimensions.values())
    ]
    assert all([LinkableElementProperty.JOINED in dim.properties for dim in filtered_dimensions]), (
        f"Found a filtered dimension that did not match the applied filter properties! "
        f"Filter properties: {filter_properties}, dimensions: {filtered_dimensions}"
    )

    filtered_metrics = [
        metric for metric in itertools.chain.from_iterable(filtered_set.path_key_to_linkable_metrics.values())
    ]
    assert all([LinkableElementProperty.JOINED in metric.properties for metric in filtered_metrics]), (
        f"Found a filtered metric that did not match the applied filter properties! "
        f"Filter properties: {filter_properties}, metrics: {filtered_metrics}"
    )

    # These should be untouched so we do a direct comparison in the assertion
    filtered_entity_keys = set(filtered_set.path_key_to_linkable_entities.keys())
    original_entity_keys = set(linkable_set.path_key_to_linkable_entities.keys())
    assert filtered_entity_keys == original_entity_keys, (
        f"Found a filter applied to entities despite the filter spec including all elements with the ENTITY property! "
        f"Filter properties: {filter_properties}, entities: {linkable_set.path_key_to_linkable_entities}, "
        f"filtered_entities: {filtered_set.path_key_to_linkable_entities}"
    )


def test_filter_without_any_of() -> None:
    """Tests behavior of filter method with a `without_any_of` specified.

    Note the filter conflict - the end result should exclude all metrics.
    """
    with_any_of_properties = frozenset(
        [LinkableElementProperty.JOINED, LinkableElementProperty.LOCAL_LINKED, LinkableElementProperty.METRIC]
    )
    without_any_of_properties = frozenset([LinkableElementProperty.ENTITY, LinkableElementProperty.METRIC])
    linkable_set = _linkable_set_with_uniques_and_duplicates()

    filtered_set = linkable_set.filter(with_any_of=with_any_of_properties, without_any_of=without_any_of_properties)

    filtered_dimensions = [
        dim for dim in itertools.chain.from_iterable(filtered_set.path_key_to_linkable_dimensions.values())
    ]
    assert all(
        [
            LinkableElementProperty.JOINED in dim.properties or LinkableElementProperty.LOCAL_LINKED in dim.properties
            for dim in filtered_dimensions
        ]
    ), (
        f"Found a filtered dimension that did not match the applied filter properties! "
        f"Included properties: {with_any_of_properties}, excluded properties: {without_any_of_properties}, "
        f"dimensions: {filtered_dimensions}"
    )
    assert len(filtered_set.path_key_to_linkable_metrics) == 0, (
        f"Found at least one metric that passed a filter which should have excluded all metric properties! "
        f"Filter: {without_any_of_properties}. Metrics: {filtered_set.path_key_to_linkable_metrics}"
    )
    assert len(filtered_set.path_key_to_linkable_entities) == 0, (
        f"Found at least one entity that passed a filter which should have excluded all entity properties! "
        f"Filter: {without_any_of_properties}. Entities: {filtered_set.path_key_to_linkable_entities}"
    )


def test_filter_without_all_of() -> None:
    """Tests behavior of filter method with a `without_all_of` specified.

    Note the filter overlap. The end result should include metrics, but not if they have JOINED.
    """
    with_any_of_properties = frozenset(
        [LinkableElementProperty.JOINED, LinkableElementProperty.LOCAL_LINKED, LinkableElementProperty.METRIC]
    )
    without_all_of_properties = frozenset([LinkableElementProperty.JOINED, LinkableElementProperty.METRIC])
    linkable_set = _linkable_set_with_uniques_and_duplicates()

    filtered_set = linkable_set.filter(with_any_of=with_any_of_properties, without_all_of=without_all_of_properties)

    filtered_entities = [
        entity for entity in itertools.chain.from_iterable(filtered_set.path_key_to_linkable_entities.values())
    ]
    assert any(LinkableElementProperty.JOINED in entity.properties for entity in filtered_entities), (
        f"At least one entity with a JOINED property was expected in the filtered output. "
        f"Filter with_any_of: {with_any_of_properties}, filter without_all_of: {without_all_of_properties}. "
        f"Entities: {linkable_set.path_key_to_linkable_entities.values()}. Filtered Entities: {filtered_entities}"
    )
    filtered_metrics = [
        metric for metric in itertools.chain.from_iterable(filtered_set.path_key_to_linkable_metrics.values())
    ]
    assert len(filtered_metrics) > 0, (
        f"At least one metric without a JOINED property was expected in the filtered output. "
        f"Filter with_any_of: {with_any_of_properties}, filter without_all_of: {without_all_of_properties}. "
        f"Metrics: {linkable_set.path_key_to_linkable_metrics.values()}. Filtered Metrics: {filtered_metrics}"
    )
    assert all([LinkableElementProperty.JOINED not in metric.properties for metric in filtered_metrics]), (
        f"Found a filtered metric that did not match the applied filter properties! "
        f"Filter properties: {without_all_of_properties}, metrics: {filtered_metrics}"
    )


def test_intersection_by_path_key() -> None:
    """Tests basic intersection operations between LinkableElementSet instances.

    The intersection behavior for the metric type, in particular, illustrates how the base class handles the case
    where the path key exists across all sets, but the values associated with it diverge. We expect the union
    of all input values in this case, since the intersection is by path key not linkable entity.
    """
    final_entities = {
        _base_entity.path_key: (_base_entity,),
        _ambiguous_entity.path_key: (_ambiguous_entity, _ambiguous_entity_with_join_path),
    }

    linkable_set = _linkable_set_with_uniques_and_duplicates()
    intermediate_set = LinkableElementSet(
        path_key_to_linkable_dimensions={
            _categorical_dimension.path_key: (_categorical_dimension,),
            _time_dimension.path_key: (_time_dimension,),
        },
        path_key_to_linkable_entities=final_entities,
        path_key_to_linkable_metrics={
            _base_metric.path_key: (_base_metric,),
            _ambiguous_metric.path_key: (_ambiguous_metric_with_join_path,),
        },
    )
    final_set = LinkableElementSet(
        path_key_to_linkable_dimensions={_categorical_dimension.path_key: (_categorical_dimension,)},
        path_key_to_linkable_entities=final_entities,
        path_key_to_linkable_metrics={_ambiguous_metric.path_key: (_ambiguous_metric,)},
    )

    intersection = LinkableElementSet.intersection_by_path_key([linkable_set, intermediate_set, final_set])

    assert {
        _categorical_dimension.path_key: (_categorical_dimension,)
    } == intersection.path_key_to_linkable_dimensions, (
        "Intersection output did not match expected minimal output for dimension elements!"
    )

    # Entity comparisons are more complicated here
    linkable_entities = intersection.path_key_to_linkable_entities
    assert (
        _base_entity.path_key in linkable_entities
    ), f"Did not find expected base entity in intersected output: {linkable_entities}!"
    assert (
        _ambiguous_entity.path_key in linkable_entities
    ), f"Did not find expected ambiguous entity in intersected output: {linkable_entities}!"
    assert (
        len(linkable_entities) == 2
    ), f"Did not get the expected number of entity entries from intersection: {linkable_entities}!"
    assert (
        len(linkable_entities[_ambiguous_entity.path_key]) == 2
    ), f"Did not get the expected number of ambiguous entity entries from intersection: {linkable_entities}!"
    assert linkable_entities[_base_entity.path_key] == (
        _base_entity,
    ), "Base entity intersection output did not match expected value!"

    # Metric comparisons demonstrate the union behavior within element path key
    linkable_metrics = intersection.path_key_to_linkable_metrics
    assert (
        len(linkable_metrics) == 1
    ), f"Did not find the expected number of metric entries from intersection: {linkable_metrics}!"
    assert (
        _ambiguous_metric.path_key in linkable_metrics
    ), f"Did not find expected ambiguous metric in intersected output!{linkable_metrics}"
    assert (
        len(linkable_metrics[_ambiguous_metric.path_key]) == 2
    ), f"Did not get the expected number of ambiguous metric entries from intersection: {linkable_metrics}!"
    expected_metrics = [_ambiguous_metric_with_join_path, _ambiguous_metric]
    assert all(metric in linkable_metrics[_ambiguous_metric.path_key] for metric in expected_metrics), (
        f"Did not find all expected metrics in ambiguous metric output. Expected: {expected_metrics}. "
        f"Actual: {linkable_metrics}."
    )


def test_merge_by_path_key() -> None:
    """Tests basic merge operations between LinkableElementSet instances.

    The merge behavior for the dimension type, in particular, illustrates how the base class handles the case where the
    path key exists with the same value in both sets. The merge operation produces a tuple of all inputs without
    deduplicating elements in the value for that path key.
    """
    first_set = LinkableElementSet(
        path_key_to_linkable_dimensions={
            _categorical_dimension.path_key: (_categorical_dimension,),
        },
        path_key_to_linkable_entities={
            _base_entity.path_key: (_base_entity,),
        },
        path_key_to_linkable_metrics={
            _base_metric.path_key: (_base_metric,),
            _ambiguous_metric.path_key: (_ambiguous_metric_with_join_path,),
        },
    )
    second_set = LinkableElementSet(
        path_key_to_linkable_dimensions={
            _categorical_dimension.path_key: (_categorical_dimension,),
            _ambiguous_categorical_dimension.path_key: (_ambiguous_categorical_dimension,),
        },
        path_key_to_linkable_entities={
            _ambiguous_entity.path_key: (_ambiguous_entity, _ambiguous_entity_with_join_path),
        },
        path_key_to_linkable_metrics={_ambiguous_metric.path_key: (_ambiguous_metric,)},
    )

    merged_set = LinkableElementSet.merge_by_path_key([first_set, second_set])

    # Dimension values demonstrate the duplication in the union of values for each path key
    merged_dimensions = merged_set.path_key_to_linkable_dimensions
    assert (
        len(merged_dimensions) == 2
    ), f"Did not get the expected number of path keys for merged dimensions! Dimensions: {merged_dimensions}"
    assert (
        _categorical_dimension.path_key in merged_dimensions
        and _ambiguous_categorical_dimension.path_key in merged_dimensions
    ), f"Did not get expected keys in merged dimensions! Dimension keys: {list(merged_dimensions.keys())}"
    assert len(merged_dimensions[_categorical_dimension.path_key]) == 2, (
        f"Did not get the expected number of values for merged categorical dimensions. Duplicate values are expected! "
        f"Categorical dimensions: {merged_dimensions[_categorical_dimension.path_key]}"
    )
    assert all(dim == _categorical_dimension for dim in merged_dimensions[_categorical_dimension.path_key]), (
        f"Found unexpected value in categorical dimension set, which should consist only of duplicate values. "
        f"Categorical dimensions found: {merged_dimensions[_categorical_dimension.path_key]}"
    )
    assert merged_dimensions[_ambiguous_categorical_dimension.path_key] == (
        _ambiguous_categorical_dimension,
    ), "Did not get expected value for merged ambiguous categorical dimension!"

    merged_entities = merged_set.path_key_to_linkable_entities
    assert (
        len(merged_entities) == 2
    ), f"Did not get the expected number of path keys for merged entities! Entities: {merged_entities}"
    assert (
        _base_entity.path_key in merged_entities and _ambiguous_entity.path_key in merged_entities
    ), f"Did not get expected keys in merged entities! Entity keys: {list(merged_entities.keys())}"
    assert merged_entities[_base_entity.path_key] == (
        _base_entity,
    ), "Did not get expected value for merged base entity!"
    assert merged_entities[_ambiguous_entity.path_key] == (
        _ambiguous_entity,
        _ambiguous_entity_with_join_path,
    ), "Did not get expected value for merged ambiguous entity!"

    merged_metrics = merged_set.path_key_to_linkable_metrics
    assert (
        len(merged_metrics) == 2
    ), f"Did not get the expected number of path keys for merged metrics! Metrics: {merged_metrics}"
    assert (
        _base_metric.path_key in merged_metrics and _ambiguous_metric.path_key in merged_metrics
    ), f"Did not get expected keys in merged metrics! Metric keys: {list(merged_metrics.keys())}"
    assert merged_metrics[_base_metric.path_key] == (
        _base_metric,
    ), "Did not get expected value for merged base metric!"
    ambiguous_metrics = merged_metrics[_ambiguous_metric.path_key]
    assert (
        len(ambiguous_metrics) == 2
        and _ambiguous_metric in ambiguous_metrics
        and _ambiguous_metric_with_join_path in ambiguous_metrics
    ), f"Did not get expected value for merged ambiguous metrics! Ambiguous metrics: {ambiguous_metrics}"


def test_only_unique_path_keys() -> None:
    """Tests behavior of only_unique_path_keys property accessor for LinkableElementSet.

    The dimension and entity sets illustrate how the function behaves with duplicate values in place.
    """
    base_set = LinkableElementSet(
        path_key_to_linkable_dimensions={
            _categorical_dimension.path_key: (_categorical_dimension, _categorical_dimension),
            _time_dimension.path_key: (_time_dimension,),
        },
        path_key_to_linkable_entities={
            _base_entity.path_key: (_base_entity,),
            _ambiguous_entity.path_key: (_ambiguous_entity, _ambiguous_entity, _ambiguous_entity_with_join_path),
        },
        path_key_to_linkable_metrics={
            _ambiguous_metric.path_key: (_ambiguous_metric, _ambiguous_metric_with_join_path)
        },
    )

    unique_path_keys = base_set.only_unique_path_keys

    assert unique_path_keys.path_key_to_linkable_dimensions == {
        _time_dimension.path_key: (_time_dimension,)
    }, "Got an unexpected value for unique dimensions sets!"
    assert unique_path_keys.path_key_to_linkable_entities == {
        _base_entity.path_key: (_base_entity,)
    }, "Got unexpected value for unique entity sets!"
    assert unique_path_keys.path_key_to_linkable_metrics == dict(), "Found unexpected unique values for metric sets!"
