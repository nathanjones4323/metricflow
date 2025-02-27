test_name: test_derived_metric_with_offset_window_and_offset_to_grain
test_filename: test_derived_metric_rendering.py
sql_engine: Databricks
---
-- Compute Metrics via Expressions
SELECT
  metric_time__day
  , month_start_bookings - bookings_1_month_ago AS bookings_month_start_compared_to_1_month_prior
FROM (
  -- Combine Aggregated Outputs
  SELECT
    COALESCE(subq_24.metric_time__day, subq_32.metric_time__day) AS metric_time__day
    , MAX(subq_24.month_start_bookings) AS month_start_bookings
    , MAX(subq_32.bookings_1_month_ago) AS bookings_1_month_ago
  FROM (
    -- Join to Time Spine Dataset
    -- Pass Only Elements: ['bookings', 'metric_time__day']
    -- Aggregate Measures
    -- Compute Metrics via Expressions
    SELECT
      subq_20.ds AS metric_time__day
      , SUM(subq_18.bookings) AS month_start_bookings
    FROM ***************************.mf_time_spine subq_20
    INNER JOIN (
      -- Read Elements From Semantic Model 'bookings_source'
      -- Metric Time Dimension 'ds'
      SELECT
        DATE_TRUNC('day', ds) AS metric_time__day
        , 1 AS bookings
      FROM ***************************.fct_bookings bookings_source_src_28000
    ) subq_18
    ON
      DATE_TRUNC('month', subq_20.ds) = subq_18.metric_time__day
    GROUP BY
      subq_20.ds
  ) subq_24
  FULL OUTER JOIN (
    -- Join to Time Spine Dataset
    -- Pass Only Elements: ['bookings', 'metric_time__day']
    -- Aggregate Measures
    -- Compute Metrics via Expressions
    SELECT
      subq_28.ds AS metric_time__day
      , SUM(subq_26.bookings) AS bookings_1_month_ago
    FROM ***************************.mf_time_spine subq_28
    INNER JOIN (
      -- Read Elements From Semantic Model 'bookings_source'
      -- Metric Time Dimension 'ds'
      SELECT
        DATE_TRUNC('day', ds) AS metric_time__day
        , 1 AS bookings
      FROM ***************************.fct_bookings bookings_source_src_28000
    ) subq_26
    ON
      DATEADD(month, -1, subq_28.ds) = subq_26.metric_time__day
    GROUP BY
      subq_28.ds
  ) subq_32
  ON
    subq_24.metric_time__day = subq_32.metric_time__day
  GROUP BY
    COALESCE(subq_24.metric_time__day, subq_32.metric_time__day)
) subq_33
