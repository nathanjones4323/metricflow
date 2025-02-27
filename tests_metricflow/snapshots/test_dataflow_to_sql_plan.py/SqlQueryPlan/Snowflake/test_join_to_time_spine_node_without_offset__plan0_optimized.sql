test_name: test_join_to_time_spine_node_without_offset
test_filename: test_dataflow_to_sql_plan.py
docstring:
  Tests JoinToTimeSpineNode for a single metric with offset_window.
sql_engine: Snowflake
---
-- Join to Time Spine Dataset
SELECT
  subq_12.metric_time__day AS metric_time__day
  , subq_11.listing AS listing
  , subq_11.booking_fees AS booking_fees
FROM (
  -- Time Spine
  SELECT
    ds AS metric_time__day
  FROM ***************************.mf_time_spine subq_13
  WHERE ds BETWEEN '2020-01-01' AND '2021-01-01'
) subq_12
INNER JOIN (
  -- Compute Metrics via Expressions
  SELECT
    metric_time__day
    , listing
    , booking_value * 0.05 AS booking_fees
  FROM (
    -- Read Elements From Semantic Model 'bookings_source'
    -- Metric Time Dimension 'ds'
    -- Pass Only Elements: ['booking_value', 'metric_time__day', 'listing']
    -- Aggregate Measures
    SELECT
      DATE_TRUNC('day', ds) AS metric_time__day
      , listing_id AS listing
      , SUM(booking_value) AS booking_value
    FROM ***************************.fct_bookings bookings_source_src_28000
    GROUP BY
      DATE_TRUNC('day', ds)
      , listing_id
  ) subq_10
) subq_11
ON
  subq_12.metric_time__day = subq_11.metric_time__day
