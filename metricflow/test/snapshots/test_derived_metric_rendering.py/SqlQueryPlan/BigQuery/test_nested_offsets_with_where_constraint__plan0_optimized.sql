-- Compute Metrics via Expressions
SELECT
  metric_time__day
  , 2 * bookings_offset_once AS bookings_offset_twice
FROM (
  -- Constrain Output with WHERE
  SELECT
    metric_time__day
    , bookings_offset_once
  FROM (
    -- Join to Time Spine Dataset
    SELECT
      subq_23.ds AS metric_time__day
      , subq_21.bookings_offset_once AS bookings_offset_once
    FROM ***************************.mf_time_spine subq_23
    INNER JOIN (
      -- Compute Metrics via Expressions
      SELECT
        metric_time__day
        , 2 * bookings AS bookings_offset_once
      FROM (
        -- Join to Time Spine Dataset
        -- Pass Only Elements: ['bookings', 'metric_time__day']
        -- Aggregate Measures
        -- Compute Metrics via Expressions
        SELECT
          subq_16.ds AS metric_time__day
          , SUM(subq_14.bookings) AS bookings
        FROM ***************************.mf_time_spine subq_16
        INNER JOIN (
          -- Read Elements From Semantic Model 'bookings_source'
          -- Metric Time Dimension 'ds'
          SELECT
            DATE_TRUNC(ds, day) AS metric_time__day
            , 1 AS bookings
          FROM ***************************.fct_bookings bookings_source_src_28001
        ) subq_14
        ON
          DATE_SUB(CAST(subq_16.ds AS DATETIME), INTERVAL 5 day) = subq_14.metric_time__day
        GROUP BY
          metric_time__day
      ) subq_20
    ) subq_21
    ON
      DATE_SUB(CAST(subq_23.ds AS DATETIME), INTERVAL 2 day) = subq_21.metric_time__day
  ) subq_24
  WHERE metric_time__day = '2020-01-12' or metric_time__day = '2020-01-13'
) subq_25
