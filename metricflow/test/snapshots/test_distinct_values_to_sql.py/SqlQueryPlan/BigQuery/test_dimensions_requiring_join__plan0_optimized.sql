-- Join Standard Outputs
-- Pass Only Elements: ['user__home_state_latest', 'listing__is_lux_latest']
SELECT
  listings_latest_src_28005.is_lux AS listing__is_lux_latest
  , users_latest_src_28009.home_state_latest AS user__home_state_latest
FROM ***************************.dim_listings_latest listings_latest_src_28005
FULL OUTER JOIN
  ***************************.dim_users_latest users_latest_src_28009
ON
  listings_latest_src_28005.user_id = users_latest_src_28009.user_id
GROUP BY
  listing__is_lux_latest
  , user__home_state_latest
