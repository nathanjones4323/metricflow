test_name: test_join_to_time_spine_with_filter_not_in_group_by_using_agg_time
test_filename: test_fill_nulls_with_rendering.py
sql_engine: BigQuery
---
-- Join to Time Spine Dataset
-- Compute Metrics via Expressions
SELECT
  subq_14.booking__ds__day AS booking__ds__day
  , subq_13.bookings AS bookings_join_to_time_spine_with_tiered_filters
FROM (
  -- Filter Time Spine
  SELECT
    booking__ds__day
  FROM (
    -- Time Spine
    SELECT
      ds AS booking__ds__day
      , DATETIME_TRUNC(ds, month) AS booking__ds__month
      , ds AS metric_time__day
    FROM ***************************.mf_time_spine subq_15
  ) subq_16
  WHERE (
    metric_time__day <= '2020-01-02'
  ) AND (
    booking__ds__month > '2020-01-01'
  )
) subq_14
LEFT OUTER JOIN (
  -- Constrain Output with WHERE
  -- Pass Only Elements: ['bookings', 'booking__ds__day']
  -- Aggregate Measures
  SELECT
    booking__ds__day
    , SUM(bookings) AS bookings
  FROM (
    -- Read Elements From Semantic Model 'bookings_source'
    -- Metric Time Dimension 'ds'
    SELECT
      DATETIME_TRUNC(ds, day) AS booking__ds__day
      , DATETIME_TRUNC(ds, month) AS booking__ds__month
      , DATETIME_TRUNC(ds, day) AS metric_time__day
      , 1 AS bookings
    FROM ***************************.fct_bookings bookings_source_src_28000
  ) subq_10
  WHERE ((metric_time__day >= '2020-01-02') AND (metric_time__day <= '2020-01-02')) AND (booking__ds__month > '2020-01-01')
  GROUP BY
    booking__ds__day
) subq_13
ON
  subq_14.booking__ds__day = subq_13.booking__ds__day
