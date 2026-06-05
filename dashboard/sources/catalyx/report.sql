-- content_md excluded here (large); fetched on demand on the reports page
select id, run_id, report_type, report_date, path, created_at
from read_parquet('../data/history/report.parquet')
