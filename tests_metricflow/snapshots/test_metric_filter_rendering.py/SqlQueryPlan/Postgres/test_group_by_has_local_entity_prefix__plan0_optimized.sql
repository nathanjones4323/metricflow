test_name: test_group_by_has_local_entity_prefix
test_filename: test_metric_filter_rendering.py
sql_engine: Postgres
---
-- Constrain Output with WHERE
-- Pass Only Elements: ['listings',]
-- Aggregate Measures
-- Compute Metrics via Expressions
SELECT
  SUM(listings) AS listings
FROM (
  -- Join Standard Outputs
  SELECT
    subq_27.listing__user__average_booking_value AS user__listing__user__average_booking_value
    , subq_17.listings AS listings
  FROM (
    -- Read Elements From Semantic Model 'listings_latest'
    -- Metric Time Dimension 'ds'
    SELECT
      user_id AS user
      , 1 AS listings
    FROM ***************************.dim_listings_latest listings_latest_src_28000
  ) subq_17
  LEFT OUTER JOIN (
    -- Join Standard Outputs
    -- Pass Only Elements: ['average_booking_value', 'listing__user']
    -- Aggregate Measures
    -- Compute Metrics via Expressions
    -- Pass Only Elements: ['listing__user', 'listing__user__average_booking_value']
    SELECT
      listings_latest_src_28000.user_id AS listing__user
      , AVG(bookings_source_src_28000.booking_value) AS listing__user__average_booking_value
    FROM ***************************.fct_bookings bookings_source_src_28000
    LEFT OUTER JOIN
      ***************************.dim_listings_latest listings_latest_src_28000
    ON
      bookings_source_src_28000.listing_id = listings_latest_src_28000.listing_id
    GROUP BY
      listings_latest_src_28000.user_id
  ) subq_27
  ON
    subq_17.user = subq_27.listing__user
) subq_28
WHERE user__listing__user__average_booking_value > 1
