# Performance Comparison Report: backend_comparison
Generated: 2025-08-09T00:26:52.005214

## Test: eval_filtered_high_selectivity_duckdb
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| duckdb | 1629.35 | 3.06 | 99.6 |

## Test: eval_filtered_high_selectivity_sqlite
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| sqlite | 1531.77 | 2.83 | 99.6 |

### Performance vs sqlite (baseline)

## Test: eval_filtered_low_selectivity_duckdb
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| duckdb | 1624.54 | 2.62 | 99.8 |

## Test: eval_filtered_low_selectivity_sqlite
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| sqlite | 1461.18 | 2.90 | 100.1 |

### Performance vs sqlite (baseline)

## Test: eval_filtered_medium_selectivity_duckdb
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| duckdb | 2121.88 | 2.87 | 90.2 |

## Test: eval_filtered_medium_selectivity_sqlite
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| sqlite | 1666.64 | 2.73 | 99.3 |

### Performance vs sqlite (baseline)

## Test: eval_high_selectivity_duckdb
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| duckdb | 1553.88 | 2.69 | 100.5 |

## Test: eval_high_selectivity_sqlite
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| sqlite | 1498.21 | 3.18 | 99.0 |

### Performance vs sqlite (baseline)

## Test: eval_low_selectivity_duckdb
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| duckdb | 1615.32 | 2.75 | 96.8 |

## Test: eval_low_selectivity_sqlite
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| sqlite | 1558.24 | 2.92 | 100.3 |

### Performance vs sqlite (baseline)

## Test: eval_medium_selectivity_duckdb
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| duckdb | 1533.09 | 2.99 | 98.6 |

## Test: eval_medium_selectivity_sqlite
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| sqlite | 1485.98 | 2.81 | 99.3 |

### Performance vs sqlite (baseline)

## Test: init_duckdb
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| duckdb | 152.64 | 1.18 | 0.0 |

## Test: init_sqlite
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| sqlite | 700.50 | 8.84 | 0.0 |

### Performance vs sqlite (baseline)

## Test: multi_eval_duckdb
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| duckdb | 4758.16 | 4.57 | 0.0 |

## Test: multi_eval_duckdb_run_0
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| duckdb | 4814.00 | 4.58 | 99.9 |

## Test: multi_eval_duckdb_run_1
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| duckdb | 4712.38 | 4.53 | 100.0 |

## Test: multi_eval_duckdb_run_2
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| duckdb | 4748.09 | 4.59 | 100.0 |

## Test: multi_eval_sqlite
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| sqlite | 4742.71 | 5.73 | 0.0 |

### Performance vs sqlite (baseline)

## Test: multi_eval_sqlite_run_0
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| sqlite | 4834.56 | 5.53 | 98.8 |

### Performance vs sqlite (baseline)

## Test: multi_eval_sqlite_run_1
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| sqlite | 4668.83 | 5.51 | 99.3 |

### Performance vs sqlite (baseline)

## Test: multi_eval_sqlite_run_2
| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |
|---------------|-------------------|------------------|--------|
| sqlite | 4724.72 | 6.15 | 99.8 |

### Performance vs sqlite (baseline)
