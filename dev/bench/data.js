window.BENCHMARK_DATA = {
  "lastUpdate": 1776811322228,
  "repoUrl": "https://github.com/next-dj/next-dj",
  "entries": {
    "next-dj benchmarks": [
      {
        "commit": {
          "author": {
            "email": "zebartcoc@gmail.com",
            "name": "Pavel Kutsenko",
            "username": "paqstd-dev"
          },
          "committer": {
            "email": "noreply@github.com",
            "name": "GitHub",
            "username": "web-flow"
          },
          "distinct": true,
          "id": "cba386c4899e6d1700a9ccc3528ba4defb0a9103",
          "message": "refactor(core): split monolithic modules and tests into focused subpackages (#104)\n\n* feat: refactored core modules\n\n* feat: changed docs\n\n* feat: refactored tests\n\n* feat: simple refactored\n\n* feat: added benchmark perfomance checks\n\n* fix: some fixes to perfomance\n\n* fix: fixed comments after auto code-review\n\n* fix: fixed examples",
          "timestamp": "2026-04-22T01:40:49+03:00",
          "tree_id": "63c56f24e58b2a45e711387d75d8b41c855885fc",
          "url": "https://github.com/next-dj/next-dj/commit/cba386c4899e6d1700a9ccc3528ba4defb0a9103"
        },
        "date": 1776811321299,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/benchmarks/apps/test_bench_autoreload.py::TestBenchAppsAutoreload::test_install_uninstall_cycle",
            "value": 137698.6690497289,
            "unit": "iter/sec",
            "range": "stddev: 0.0001113549801153763",
            "extra": "mean: 7.262234318611004 usec\nrounds: 34372"
          },
          {
            "name": "tests/benchmarks/apps/test_bench_autoreload.py::TestBenchAppsAutoreload::test_install_idempotent",
            "value": 9722880.39052076,
            "unit": "iter/sec",
            "range": "stddev: 1.0322661874026308e-8",
            "extra": "mean: 102.85018017653918 nsec\nrounds: 97571"
          },
          {
            "name": "tests/benchmarks/components/test_bench_backends.py::TestBenchComponentScanner::test_scan_small",
            "value": 3165.76526835035,
            "unit": "iter/sec",
            "range": "stddev: 0.0000133427084041618",
            "extra": "mean: 315.87938941571946 usec\nrounds: 2532"
          },
          {
            "name": "tests/benchmarks/components/test_bench_backends.py::TestBenchComponentScanner::test_scan_large",
            "value": 63.696252543469505,
            "unit": "iter/sec",
            "range": "stddev: 0.004301468390139351",
            "extra": "mean: 15.6995107258084 msec\nrounds: 62"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentRegistry::test_register_bulk",
            "value": 10278.247836726732,
            "unit": "iter/sec",
            "range": "stddev: 0.000004551850830370186",
            "extra": "mean: 97.29284756364325 usec\nrounds: 6875"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentRegistry::test_lookup_by_name_hit",
            "value": 8497699.394412022,
            "unit": "iter/sec",
            "range": "stddev: 1.4205790091435591e-8",
            "extra": "mean: 117.67890973615602 nsec\nrounds: 80109"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentRegistry::test_lookup_miss",
            "value": 9413027.669450235,
            "unit": "iter/sec",
            "range": "stddev: 1.0965532485878851e-8",
            "extra": "mean: 106.23574423832589 nsec\nrounds: 92860"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentVisibility::test_visibility_resolve_cold",
            "value": 2401.9303291301694,
            "unit": "iter/sec",
            "range": "stddev: 0.000012612427718611297",
            "extra": "mean: 416.3318094085344 usec\nrounds: 829"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentVisibility::test_visibility_resolve_cached",
            "value": 1756001.5484864542,
            "unit": "iter/sec",
            "range": "stddev: 8.902884313035403e-8",
            "extra": "mean: 569.4755798261838 nsec\nrounds: 179824"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentVisibility::test_version_bump_invalidation",
            "value": 793.326795212337,
            "unit": "iter/sec",
            "range": "stddev: 0.0016560719361966066",
            "extra": "mean: 1.2605145899961014 msec\nrounds: 2539"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_merge_cold",
            "value": 44546.23091177508,
            "unit": "iter/sec",
            "range": "stddev: 0.0000020476222572614785",
            "extra": "mean: 22.44858834365864 usec\nrounds: 23867"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_merge_warm_cached",
            "value": 9713853.83055452,
            "unit": "iter/sec",
            "range": "stddev: 1.0702293705624922e-8",
            "extra": "mean: 102.94575329665163 nsec\nrounds: 96164"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_attribute_access_cached",
            "value": 5602484.3267699005,
            "unit": "iter/sec",
            "range": "stddev: 1.5209713490556752e-8",
            "extra": "mean: 178.49224409638782 nsec\nrounds: 56076"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_reload_cycle",
            "value": 9229.130695275782,
            "unit": "iter/sec",
            "range": "stddev: 0.00000849628928071664",
            "extra": "mean: 108.35256678203518 usec\nrounds: 6918"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_register_bulk",
            "value": 6823.501581130728,
            "unit": "iter/sec",
            "range": "stddev: 0.000006491529214478736",
            "extra": "mean: 146.55232187024558 usec\nrounds: 5176"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_get_meta_hit",
            "value": 6701636.233622206,
            "unit": "iter/sec",
            "range": "stddev: 1.322726753292233e-8",
            "extra": "mean: 149.21729039588652 nsec\nrounds: 65755"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_get_meta_miss",
            "value": 7075675.868868532,
            "unit": "iter/sec",
            "range": "stddev: 1.583969983412736e-8",
            "extra": "mean: 141.32925511749164 nsec\nrounds: 69219"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_generate_urls_with_actions",
            "value": 207524.07123564137,
            "unit": "iter/sec",
            "range": "stddev: 9.729011349526066e-7",
            "extra": "mean: 4.818718108438181 usec\nrounds: 4821"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_none",
            "value": 6221555.552584301,
            "unit": "iter/sec",
            "range": "stddev: 2.4354317575605467e-7",
            "extra": "mean: 160.73150702393417 nsec\nrounds: 47964"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_httpresponse",
            "value": 7878723.226241242,
            "unit": "iter/sec",
            "range": "stddev: 1.1828970303921324e-8",
            "extra": "mean: 126.92411844971953 nsec\nrounds: 78413"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_str",
            "value": 6699800.037906533,
            "unit": "iter/sec",
            "range": "stddev: 1.3651922473662464e-8",
            "extra": "mean: 149.25818596706463 nsec\nrounds: 66366"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_redirect_duck",
            "value": 160131.89776772508,
            "unit": "iter/sec",
            "range": "stddev: 0.0000010735222815269843",
            "extra": "mean: 6.244851987269411 usec\nrounds: 14019"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_filter_reserved_url_kwargs",
            "value": 471938.48127152334,
            "unit": "iter/sec",
            "range": "stddev: 0.0000016927449966302212",
            "extra": "mean: 2.1189202399976867 usec\nrounds: 162312"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_url_kwargs_from_post",
            "value": 124792.54329324304,
            "unit": "iter/sec",
            "range": "stddev: 9.831481752525534e-7",
            "extra": "mean: 8.013299301466722 usec\nrounds: 59415"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchPythonModuleLoader::test_python_load_cold",
            "value": 3721.1673611944716,
            "unit": "iter/sec",
            "range": "stddev: 0.00011189062954674321",
            "extra": "mean: 268.73287410513194 usec\nrounds: 2653"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchPythonModuleLoader::test_python_load_warm_mtime_hit",
            "value": 320307.90339371865,
            "unit": "iter/sec",
            "range": "stddev: 6.770798969156928e-7",
            "extra": "mean: 3.1219960213432887 usec\nrounds: 139998"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchPythonModuleLoader::test_python_template_loader_can_load",
            "value": 300345.8871081795,
            "unit": "iter/sec",
            "range": "stddev: 7.618102216339322e-7",
            "extra": "mean: 3.3294945691725655 usec\nrounds: 120701"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchDjxLoader::test_djx_can_load_hit",
            "value": 94280.32408690012,
            "unit": "iter/sec",
            "range": "stddev: 0.000002050363418471901",
            "extra": "mean: 10.60666697622167 usec\nrounds: 36616"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchDjxLoader::test_djx_can_load_miss",
            "value": 96206.78641882591,
            "unit": "iter/sec",
            "range": "stddev: 0.0000018111211673159383",
            "extra": "mean: 10.394277131829426 usec\nrounds: 25966"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchDjxLoader::test_djx_load_template",
            "value": 40909.09658667804,
            "unit": "iter/sec",
            "range": "stddev: 0.0000038159443311099925",
            "extra": "mean: 24.444441051911372 usec\nrounds: 18440"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLayoutLoader::test_ancestor_walk_no_layouts",
            "value": 4770.836151504143,
            "unit": "iter/sec",
            "range": "stddev: 0.000018210560720355018",
            "extra": "mean: 209.60686308305122 usec\nrounds: 2381"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLayoutLoader::test_ancestor_walk_with_layouts",
            "value": 4733.632553964994,
            "unit": "iter/sec",
            "range": "stddev: 0.00001163255668669687",
            "extra": "mean: 211.2542510639906 usec\nrounds: 2820"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_manager.py::TestBenchPageRender::test_render_simple",
            "value": 3332.041065586734,
            "unit": "iter/sec",
            "range": "stddev: 0.000027813634563854157",
            "extra": "mean: 300.11634920349087 usec\nrounds: 63"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_manager.py::TestBenchPageRender::test_render_heavy_context",
            "value": 1142.2924686486754,
            "unit": "iter/sec",
            "range": "stddev: 0.00002053254453940292",
            "extra": "mean: 875.4325424057059 usec\nrounds: 507"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_manager.py::TestBenchPageRender::test_build_render_context",
            "value": 8104.4748737450045,
            "unit": "iter/sec",
            "range": "stddev: 0.00000954038423882838",
            "extra": "mean: 123.38862364044928 usec\nrounds: 5609"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_registry.py::TestBenchPageContextRegistry::test_register_context",
            "value": 67206.53093044188,
            "unit": "iter/sec",
            "range": "stddev: 0.0000015408971830336941",
            "extra": "mean: 14.879506294336043 usec\nrounds: 23354"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_registry.py::TestBenchPageContextRegistry::test_collect_context_single",
            "value": 10966.22165287129,
            "unit": "iter/sec",
            "range": "stddev: 0.000006904162336002786",
            "extra": "mean: 91.18911067588803 usec\nrounds: 1021"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_registry.py::TestBenchPageContextRegistry::test_collect_context_keyed_many",
            "value": 3752.087465635744,
            "unit": "iter/sec",
            "range": "stddev: 0.000010132829609657363",
            "extra": "mean: 266.51830725128434 usec\nrounds: 2965"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchTreeSignature::test_signature_small",
            "value": 2158.439833636658,
            "unit": "iter/sec",
            "range": "stddev: 0.00006102261869067486",
            "extra": "mean: 463.29760247018106 usec\nrounds: 1781"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchTreeSignature::test_signature_large",
            "value": 113.72493358988419,
            "unit": "iter/sec",
            "range": "stddev: 0.000052312309341553244",
            "extra": "mean: 8.793146484536173 msec\nrounds: 97"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchCollectRoutes::test_collect_routes_cached",
            "value": 1059.074481139724,
            "unit": "iter/sec",
            "range": "stddev: 0.00001154236961640863",
            "extra": "mean: 944.2206547398338 usec\nrounds: 1034"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchCollectRoutes::test_collect_routes_fresh",
            "value": 143.25572497629432,
            "unit": "iter/sec",
            "range": "stddev: 0.00015429405684789594",
            "extra": "mean: 6.9805238161719405 msec\nrounds: 136"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_unique_urls",
            "value": 29975.163648539306,
            "unit": "iter/sec",
            "range": "stddev: 0.000002351973603200712",
            "extra": "mean: 33.3609521444174 usec\nrounds: 12099"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_dedup_hit",
            "value": 52208.90674201473,
            "unit": "iter/sec",
            "range": "stddev: 0.0000015422158989875353",
            "extra": "mean: 19.153819959138453 usec\nrounds: 39641"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_inline_unique",
            "value": 27663.031684042868,
            "unit": "iter/sec",
            "range": "stddev: 0.0000023743483875871795",
            "extra": "mean: 36.1493277895799 usec\nrounds: 17136"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_js_context_many",
            "value": 3941.675363007729,
            "unit": "iter/sec",
            "range": "stddev: 0.000027424638646745755",
            "extra": "mean: 253.69922885707703 usec\nrounds: 3500"
          },
          {
            "name": "tests/benchmarks/static/test_bench_discovery.py::TestBenchPathResolver::test_find_page_root_hit_cached",
            "value": 5674085.099821416,
            "unit": "iter/sec",
            "range": "stddev: 2.5557273448442727e-8",
            "extra": "mean: 176.23986641149844 nsec\nrounds: 193462"
          },
          {
            "name": "tests/benchmarks/static/test_bench_discovery.py::TestBenchPathResolver::test_logical_name_for_template_deep",
            "value": 1213658.1080710932,
            "unit": "iter/sec",
            "range": "stddev: 2.9771334674115436e-7",
            "extra": "mean: 823.95527484205 nsec\nrounds: 170620"
          },
          {
            "name": "tests/benchmarks/static/test_bench_discovery.py::TestBenchPathResolver::test_logical_name_for_layout_deep",
            "value": 1263054.6797576225,
            "unit": "iter/sec",
            "range": "stddev: 1.7716128223786977e-7",
            "extra": "mean: 791.7313605076051 nsec\nrounds: 173581"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_backends.py::TestBenchFileRouter::test_filerouter_generate_small",
            "value": 145.87861524121783,
            "unit": "iter/sec",
            "range": "stddev: 0.00009151309445780206",
            "extra": "mean: 6.855014344264568 msec\nrounds: 61"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_backends.py::TestBenchFileRouter::test_filerouter_generate_medium",
            "value": 31.49310284467883,
            "unit": "iter/sec",
            "range": "stddev: 0.0013838325841058399",
            "extra": "mean: 31.752984294113876 msec\nrounds: 17"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_backends.py::TestBenchFileRouter::test_filerouter_generate_large",
            "value": 2.7628897956896723,
            "unit": "iter/sec",
            "range": "stddev: 0.007456917654061534",
            "extra": "mean: 361.93987960000413 msec\nrounds: 5"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_parse_simple_segment",
            "value": 1890247.4081752205,
            "unit": "iter/sec",
            "range": "stddev: 5.739125374721848e-8",
            "extra": "mean: 529.0312768979619 nsec\nrounds: 93633"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_parse_typed_converter",
            "value": 287366.6343977884,
            "unit": "iter/sec",
            "range": "stddev: 6.957714127010158e-7",
            "extra": "mean: 3.4798751152708496 usec\nrounds: 53065"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_prepare_url_name",
            "value": 685685.1406120764,
            "unit": "iter/sec",
            "range": "stddev: 4.2089446083533604e-7",
            "extra": "mean: 1.4583953199092963 usec\nrounds: 165810"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_regex_compile_many",
            "value": 28084.743107720653,
            "unit": "iter/sec",
            "range": "stddev: 0.0000022017832325631007",
            "extra": "mean: 35.60652116932109 usec\nrounds: 25792"
          }
        ]
      }
    ]
  }
}