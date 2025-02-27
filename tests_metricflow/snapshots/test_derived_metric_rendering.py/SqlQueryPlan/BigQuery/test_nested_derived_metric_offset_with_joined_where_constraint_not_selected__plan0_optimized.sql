test_name: test_nested_derived_metric_offset_with_joined_where_constraint_not_selected
test_filename: test_derived_metric_rendering.py
sql_engine: BigQuery
---
-- Compute Metrics via Expressions
SELECT
  metric_time__day
  , 2 * bookings_offset_once AS bookings_offset_twice
FROM (
  -- Constrain Output with WHERE
  -- Pass Only Elements: ['metric_time__day', 'bookings_offset_once']
  SELECT
    metric_time__day
    , bookings_offset_once
  FROM (
    -- Join to Time Spine Dataset
    SELECT
      subq_24.ds AS metric_time__day
      , subq_22.booking__is_instant AS booking__is_instant
      , subq_22.bookings_offset_once AS bookings_offset_once
    FROM ***************************.mf_time_spine subq_24
    INNER JOIN (
      -- Compute Metrics via Expressions
      SELECT
        metric_time__day
        , booking__is_instant
        , 2 * bookings AS bookings_offset_once
      FROM (
        -- Join to Time Spine Dataset
        -- Pass Only Elements: ['bookings', 'booking__is_instant', 'metric_time__day']
        -- Aggregate Measures
        -- Compute Metrics via Expressions
        SELECT
          subq_17.ds AS metric_time__day
          , subq_15.booking__is_instant AS booking__is_instant
          , SUM(subq_15.bookings) AS bookings
        FROM ***************************.mf_time_spine subq_17
        INNER JOIN (
          -- Read Elements From Semantic Model 'bookings_source'
          -- Metric Time Dimension 'ds'
          SELECT
            DATETIME_TRUNC(ds, day) AS metric_time__day
            , is_instant AS booking__is_instant
            , 1 AS bookings
          FROM ***************************.fct_bookings bookings_source_src_28000
        ) subq_15
        ON
          DATE_SUB(CAST(subq_17.ds AS DATETIME), INTERVAL 5 day) = subq_15.metric_time__day
        GROUP BY
          metric_time__day
          , booking__is_instant
      ) subq_21
    ) subq_22
    ON
      DATE_SUB(CAST(subq_24.ds AS DATETIME), INTERVAL 2 day) = subq_22.metric_time__day
  ) subq_25
  WHERE booking__is_instant
) subq_27
