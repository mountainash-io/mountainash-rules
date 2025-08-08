# Performance Comparison Report: backend_comparison
Generated: 2025-08-08T23:50:44.605877

## Test: eval_filtered_high_selectivity_duckdb
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| duckdb | 2269.55 | 3.50 | 98.8 |

## Test: eval_filtered_high_selectivity_sqlite
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| sqlite | 2143.84 | 3.49 | 99.5 |

### Performance vs sqlite (baseline)

## Test: eval_filtered_low_selectivity_duckdb
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| duckdb | 2534.27 | 3.52 | 95.6 |

## Test: eval_filtered_low_selectivity_sqlite
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| sqlite | 2166.70 | 3.48 | 99.6 |

### Performance vs sqlite (baseline)

## Test: eval_filtered_medium_selectivity_duckdb
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| duckdb | 2397.90 | 3.87 | 98.5 |

## Test: eval_filtered_medium_selectivity_sqlite
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| sqlite | 2171.90 | 3.76 | 99.0 |

### Performance vs sqlite (baseline)

## Test: eval_high_selectivity_duckdb
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| duckdb | 2177.83 | 3.70 | 100.0 |

## Test: eval_high_selectivity_sqlite
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| sqlite | 2154.29 | 3.95 | 98.9 |

### Performance vs sqlite (baseline)

## Test: eval_low_selectivity_duckdb
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| duckdb | 2147.79 | 3.68 | 100.2 |

## Test: eval_low_selectivity_sqlite
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| sqlite | 2075.18 | 3.75 | 99.8 |

### Performance vs sqlite (baseline)

## Test: eval_medium_selectivity_duckdb
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| duckdb | 2108.82 | 3.48 | 99.6 |

## Test: eval_medium_selectivity_sqlite
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| sqlite | 2118.87 | 3.50 | 100.2 |

### Performance vs sqlite (baseline)

## Test: init_duckdb
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| duckdb | 177.73 | 1.21 | 0.0 |

## Test: init_sqlite
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| sqlite | 658.05 | 8.84 | 0.0 |

### Performance vs sqlite (baseline)

## Test: multi_eval_duckdb
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| duckdb | 6615.15 | 6.14 | 0.0 |

## Test: multi_eval_duckdb_run_0
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| duckdb | 6556.49 | 5.98 | 99.1 |

## Test: multi_eval_duckdb_run_1
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| duckdb | 6656.68 | 6.48 | 99.6 |

## Test: multi_eval_duckdb_run_2
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| duckdb | 6632.29 | 5.97 | 100.0 |

## Test: multi_eval_sqlite
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| sqlite | 6436.05 | 6.70 | 0.0 |

### Performance vs sqlite (baseline)

## Test: multi_eval_sqlite_run_0
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| sqlite | 6241.72 | 7.15 | 99.9 |

### Performance vs sqlite (baseline)

## Test: multi_eval_sqlite_run_1
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| sqlite | 6642.38 | 6.04 | 99.1 |

### Performance vs sqlite (baseline)

## Test: multi_eval_sqlite_run_2
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| sqlite | 6424.05 | 6.92 | 99.7 |

### Performance vs sqlite (baseline)
