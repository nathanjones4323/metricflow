test_name: test_metric_time_only
test_filename: test_metric_time_without_metrics.py
docstring:
  Tests querying only metric time.
sql_engine: Trino
---
-- Time Spine
-- Metric Time Dimension 'ds'
-- Pass Only Elements: ['metric_time__day',]
SELECT
  DATE_TRUNC('day', ds) AS metric_time__day
FROM ***************************.mf_time_spine time_spine_src_28006
GROUP BY
  DATE_TRUNC('day', ds)
