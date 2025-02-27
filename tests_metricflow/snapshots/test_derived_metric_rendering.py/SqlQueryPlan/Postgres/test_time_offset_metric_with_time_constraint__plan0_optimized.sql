test_name: test_time_offset_metric_with_time_constraint
test_filename: test_derived_metric_rendering.py
sql_engine: Postgres
---
-- Compute Metrics via Expressions
SELECT
  metric_time__day
  , bookings_5_days_ago AS bookings_5_day_lag
FROM (
  -- Join to Time Spine Dataset
  -- Pass Only Elements: ['bookings', 'metric_time__day']
  -- Aggregate Measures
  -- Compute Metrics via Expressions
  SELECT
    subq_10.metric_time__day AS metric_time__day
    , SUM(subq_9.bookings) AS bookings_5_days_ago
  FROM (
    -- Time Spine
    SELECT
      ds AS metric_time__day
    FROM ***************************.mf_time_spine subq_11
    WHERE ds BETWEEN '2019-12-19' AND '2020-01-02'
  ) subq_10
  INNER JOIN (
    -- Read Elements From Semantic Model 'bookings_source'
    -- Metric Time Dimension 'ds'
    SELECT
      DATE_TRUNC('day', ds) AS metric_time__day
      , 1 AS bookings
    FROM ***************************.fct_bookings bookings_source_src_28000
  ) subq_9
  ON
    subq_10.metric_time__day - MAKE_INTERVAL(days => 5) = subq_9.metric_time__day
  GROUP BY
    subq_10.metric_time__day
) subq_15
