test_name: test_join_to_time_spine_with_filters
test_filename: test_fill_nulls_with_rendering.py
sql_engine: Postgres
---
-- Compute Metrics via Expressions
SELECT
  metric_time__day
  , COALESCE(bookings, 0) AS bookings_fill_nulls_with_0
FROM (
  -- Join to Time Spine Dataset
  -- Constrain Time Range to [2020-01-03T00:00:00, 2020-01-05T00:00:00]
  SELECT
    subq_17.metric_time__day AS metric_time__day
    , subq_16.bookings AS bookings
  FROM (
    -- Filter Time Spine
    SELECT
      metric_time__day
    FROM (
      -- Time Spine
      SELECT
        ds AS metric_time__day
        , DATE_TRUNC('week', ds) AS metric_time__week
      FROM ***************************.mf_time_spine subq_18
      WHERE ds BETWEEN '2020-01-03' AND '2020-01-05'
    ) subq_19
    WHERE metric_time__week > '2020-01-01'
  ) subq_17
  LEFT OUTER JOIN (
    -- Constrain Output with WHERE
    -- Pass Only Elements: ['bookings', 'metric_time__day']
    -- Aggregate Measures
    SELECT
      metric_time__day
      , SUM(bookings) AS bookings
    FROM (
      -- Read Elements From Semantic Model 'bookings_source'
      -- Metric Time Dimension 'ds'
      -- Constrain Time Range to [2020-01-03T00:00:00, 2020-01-05T00:00:00]
      SELECT
        DATE_TRUNC('day', ds) AS metric_time__day
        , DATE_TRUNC('week', ds) AS metric_time__week
        , 1 AS bookings
      FROM ***************************.fct_bookings bookings_source_src_28000
      WHERE DATE_TRUNC('day', ds) BETWEEN '2020-01-03' AND '2020-01-05'
    ) subq_13
    WHERE metric_time__week > '2020-01-01'
    GROUP BY
      metric_time__day
  ) subq_16
  ON
    subq_17.metric_time__day = subq_16.metric_time__day
  WHERE subq_17.metric_time__day BETWEEN '2020-01-03' AND '2020-01-05'
) subq_21
