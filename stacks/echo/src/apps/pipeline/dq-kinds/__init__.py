# dq-kinds — one module per check kind, each exposes run_<kind>(spark, config, **loaders)
# returning a metrics dict { status, orphan_count?, rows_deleted?, message? }.
