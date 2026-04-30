window.BENCHMARK_DATA = {
  "lastUpdate": 1777576357789,
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
      },
      {
        "commit": {
          "author": {
            "email": "zebartcoc@gmail.com",
            "name": "Pavel Kutsenko",
            "username": "paqstd-dev"
          },
          "committer": {
            "email": "zebartcoc@gmail.com",
            "name": "Pavel Kutsenko",
            "username": "paqstd-dev"
          },
          "distinct": true,
          "id": "3a4553c5b5bbbb155b51dd44e301b1237c1c7eea",
          "message": "release: v0.5.0",
          "timestamp": "2026-04-24T00:16:58+03:00",
          "tree_id": "efa895beb21d3b14d9d9d7529f363612a44ad71f",
          "url": "https://github.com/next-dj/next-dj/commit/3a4553c5b5bbbb155b51dd44e301b1237c1c7eea"
        },
        "date": 1776979154120,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/benchmarks/apps/test_bench_autoreload.py::TestBenchAppsAutoreload::test_install_uninstall_cycle",
            "value": 140954.4123845548,
            "unit": "iter/sec",
            "range": "stddev: 0.00010912656001231964",
            "extra": "mean: 7.094492347439106 usec\nrounds: 57301"
          },
          {
            "name": "tests/benchmarks/apps/test_bench_autoreload.py::TestBenchAppsAutoreload::test_install_idempotent",
            "value": 10188893.290875424,
            "unit": "iter/sec",
            "range": "stddev: 1.0298590863429373e-8",
            "extra": "mean: 98.1460862776472 nsec\nrounds: 96062"
          },
          {
            "name": "tests/benchmarks/components/test_bench_backends.py::TestBenchComponentScanner::test_scan_small",
            "value": 3240.0738106050994,
            "unit": "iter/sec",
            "range": "stddev: 0.000023470199052420252",
            "extra": "mean: 308.63494428024933 usec\nrounds: 2369"
          },
          {
            "name": "tests/benchmarks/components/test_bench_backends.py::TestBenchComponentScanner::test_scan_large",
            "value": 65.56117907039032,
            "unit": "iter/sec",
            "range": "stddev: 0.0041998510087057",
            "extra": "mean: 15.252928854838643 msec\nrounds: 62"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentRegistry::test_register_bulk",
            "value": 10354.556789555292,
            "unit": "iter/sec",
            "range": "stddev: 0.000014513219534541563",
            "extra": "mean: 96.57583808982596 usec\nrounds: 6973"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentRegistry::test_lookup_by_name_hit",
            "value": 9011235.09854147,
            "unit": "iter/sec",
            "range": "stddev: 1.1043122153376422e-8",
            "extra": "mean: 110.97257912645702 nsec\nrounds: 93458"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentRegistry::test_lookup_miss",
            "value": 9488125.261100134,
            "unit": "iter/sec",
            "range": "stddev: 1.0161717815147068e-8",
            "extra": "mean: 105.39489862131641 nsec\nrounds: 55485"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentVisibility::test_visibility_resolve_cold",
            "value": 2491.6416125886203,
            "unit": "iter/sec",
            "range": "stddev: 0.00002092167015641227",
            "extra": "mean: 401.34182819377395 usec\nrounds: 908"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentVisibility::test_visibility_resolve_cached",
            "value": 1833682.576073671,
            "unit": "iter/sec",
            "range": "stddev: 7.956490632302265e-8",
            "extra": "mean: 545.3506583136249 nsec\nrounds: 193837"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentVisibility::test_version_bump_invalidation",
            "value": 766.0876405942337,
            "unit": "iter/sec",
            "range": "stddev: 0.001545012430345233",
            "extra": "mean: 1.3053336811755984 msec\nrounds: 2688"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_merge_cold",
            "value": 46308.79828337321,
            "unit": "iter/sec",
            "range": "stddev: 0.0000020617181655765552",
            "extra": "mean: 21.594168647624823 usec\nrounds: 22716"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_merge_warm_cached",
            "value": 9917506.940001348,
            "unit": "iter/sec",
            "range": "stddev: 9.890464869149651e-9",
            "extra": "mean: 100.83179230927405 nsec\nrounds: 97857"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_attribute_access_cached",
            "value": 5779859.307659756,
            "unit": "iter/sec",
            "range": "stddev: 2.1474214503135585e-8",
            "extra": "mean: 173.01459201173813 nsec\nrounds: 55982"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_reload_cycle",
            "value": 9859.191780302623,
            "unit": "iter/sec",
            "range": "stddev: 0.000008252974964663839",
            "extra": "mean: 101.42819231875268 usec\nrounds: 6978"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_register_bulk",
            "value": 7346.347150485579,
            "unit": "iter/sec",
            "range": "stddev: 0.000008047464739384598",
            "extra": "mean: 136.12207257778473 usec\nrounds: 5594"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_get_meta_hit",
            "value": 6773818.710578819,
            "unit": "iter/sec",
            "range": "stddev: 2.4323664721803228e-8",
            "extra": "mean: 147.6272163053727 nsec\nrounds: 193799"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_get_meta_miss",
            "value": 7286358.674061473,
            "unit": "iter/sec",
            "range": "stddev: 1.3693954548605302e-8",
            "extra": "mean: 137.24276346151817 nsec\nrounds: 74935"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_generate_urls_with_actions",
            "value": 207315.33134323673,
            "unit": "iter/sec",
            "range": "stddev: 0.0000011767057746613543",
            "extra": "mean: 4.82356993822311 usec\nrounds: 5176"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_none",
            "value": 14206424.697087986,
            "unit": "iter/sec",
            "range": "stddev: 8.26965543401883e-9",
            "extra": "mean: 70.39068740532436 nsec\nrounds: 136557"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_httpresponse",
            "value": 8277870.570644722,
            "unit": "iter/sec",
            "range": "stddev: 1.1910015793684677e-8",
            "extra": "mean: 120.80401492942345 nsec\nrounds: 78101"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_str",
            "value": 6916009.495914179,
            "unit": "iter/sec",
            "range": "stddev: 1.3160774488859868e-8",
            "extra": "mean: 144.59205132537446 nsec\nrounds: 70842"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_redirect_duck",
            "value": 172367.95694510837,
            "unit": "iter/sec",
            "range": "stddev: 0.0000011130636968172574",
            "extra": "mean: 5.801542338396783 usec\nrounds: 17679"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_filter_reserved_url_kwargs",
            "value": 524674.7278608833,
            "unit": "iter/sec",
            "range": "stddev: 3.9773197733116577e-7",
            "extra": "mean: 1.90594276205571 usec\nrounds: 149656"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_url_kwargs_from_post",
            "value": 131007.68109143243,
            "unit": "iter/sec",
            "range": "stddev: 9.196420046380304e-7",
            "extra": "mean: 7.633140222534611 usec\nrounds: 58507"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchPythonModuleLoader::test_python_load_cold",
            "value": 3926.2041645727427,
            "unit": "iter/sec",
            "range": "stddev: 0.00011331806558080823",
            "extra": "mean: 254.69893008195663 usec\nrounds: 2932"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchPythonModuleLoader::test_python_load_warm_mtime_hit",
            "value": 337504.54066139937,
            "unit": "iter/sec",
            "range": "stddev: 7.009685790736332e-7",
            "extra": "mean: 2.9629231003539225 usec\nrounds: 150989"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchPythonModuleLoader::test_python_template_loader_can_load",
            "value": 324156.2695514626,
            "unit": "iter/sec",
            "range": "stddev: 6.112276909511016e-7",
            "extra": "mean: 3.084931849023643 usec\nrounds: 139998"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchDjxLoader::test_djx_can_load_hit",
            "value": 98974.35221806284,
            "unit": "iter/sec",
            "range": "stddev: 0.0000017307230811349556",
            "extra": "mean: 10.103627632710081 usec\nrounds: 41212"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchDjxLoader::test_djx_can_load_miss",
            "value": 97956.69226063965,
            "unit": "iter/sec",
            "range": "stddev: 0.0000017487274459778187",
            "extra": "mean: 10.208592970240725 usec\nrounds: 32661"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchDjxLoader::test_djx_load_template",
            "value": 43331.241616877756,
            "unit": "iter/sec",
            "range": "stddev: 0.0000034592468806874783",
            "extra": "mean: 23.078037062535834 usec\nrounds: 20263"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLayoutLoader::test_ancestor_walk_no_layouts",
            "value": 5136.680519307537,
            "unit": "iter/sec",
            "range": "stddev: 0.000013320685348938143",
            "extra": "mean: 194.67825500169658 usec\nrounds: 2749"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLayoutLoader::test_ancestor_walk_with_layouts",
            "value": 5070.139719145837,
            "unit": "iter/sec",
            "range": "stddev: 0.000013669501825363638",
            "extra": "mean: 197.23322342061002 usec\nrounds: 3245"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_manager.py::TestBenchPageRender::test_render_simple",
            "value": 3615.185992811685,
            "unit": "iter/sec",
            "range": "stddev: 0.000023018292290347147",
            "extra": "mean: 276.610941176572 usec\nrounds: 68"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_manager.py::TestBenchPageRender::test_render_heavy_context",
            "value": 1217.494811767074,
            "unit": "iter/sec",
            "range": "stddev: 0.00003551764893416394",
            "extra": "mean: 821.3587362631946 usec\nrounds: 546"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_manager.py::TestBenchPageRender::test_build_render_context",
            "value": 8581.588366001404,
            "unit": "iter/sec",
            "range": "stddev: 0.00000942605353418872",
            "extra": "mean: 116.5285442916147 usec\nrounds: 6254"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_registry.py::TestBenchPageContextRegistry::test_register_context",
            "value": 73863.27362554477,
            "unit": "iter/sec",
            "range": "stddev: 0.0000014876169649325122",
            "extra": "mean: 13.538528025031393 usec\nrounds: 28421"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_registry.py::TestBenchPageContextRegistry::test_collect_context_single",
            "value": 12135.342274715049,
            "unit": "iter/sec",
            "range": "stddev: 0.0000068888213853462145",
            "extra": "mean: 82.40393862508348 usec\nrounds: 1222"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_registry.py::TestBenchPageContextRegistry::test_collect_context_keyed_many",
            "value": 3975.4085105243607,
            "unit": "iter/sec",
            "range": "stddev: 0.000015232350278697402",
            "extra": "mean: 251.54647562700393 usec\nrounds: 3549"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchTreeSignature::test_signature_small",
            "value": 2394.9155746595234,
            "unit": "iter/sec",
            "range": "stddev: 0.000020013854723297916",
            "extra": "mean: 417.551253405735 usec\nrounds: 1835"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchTreeSignature::test_signature_large",
            "value": 119.99309332964992,
            "unit": "iter/sec",
            "range": "stddev: 0.00032926957813989293",
            "extra": "mean: 8.333812990825724 msec\nrounds: 109"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchCollectRoutes::test_collect_routes_cached",
            "value": 1122.7871898504375,
            "unit": "iter/sec",
            "range": "stddev: 0.00003634040935985678",
            "extra": "mean: 890.6407278597528 usec\nrounds: 1084"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchCollectRoutes::test_collect_routes_fresh",
            "value": 153.30889119035808,
            "unit": "iter/sec",
            "range": "stddev: 0.00024031410861219274",
            "extra": "mean: 6.522778895832835 msec\nrounds: 144"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_unique_urls",
            "value": 31775.139144078144,
            "unit": "iter/sec",
            "range": "stddev: 0.0000023851319144189617",
            "extra": "mean: 31.47114464127744 usec\nrounds: 17215"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_dedup_hit",
            "value": 54976.2746576986,
            "unit": "iter/sec",
            "range": "stddev: 0.0000014801656801365407",
            "extra": "mean: 18.18966465491428 usec\nrounds: 24357"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_inline_unique",
            "value": 29347.175030787796,
            "unit": "iter/sec",
            "range": "stddev: 0.00000341866809051716",
            "extra": "mean: 34.074829994740924 usec\nrounds: 20717"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_js_context_many",
            "value": 4139.755780295025,
            "unit": "iter/sec",
            "range": "stddev: 0.00001695028477089259",
            "extra": "mean: 241.56014341713987 usec\nrounds: 3828"
          },
          {
            "name": "tests/benchmarks/static/test_bench_discovery.py::TestBenchPathResolver::test_find_page_root_hit_cached",
            "value": 6020130.682233378,
            "unit": "iter/sec",
            "range": "stddev: 1.4019690905022885e-8",
            "extra": "mean: 166.10935090681704 nsec\nrounds: 60053"
          },
          {
            "name": "tests/benchmarks/static/test_bench_discovery.py::TestBenchPathResolver::test_logical_name_for_template_deep",
            "value": 1303072.3023152514,
            "unit": "iter/sec",
            "range": "stddev: 2.987821596998037e-7",
            "extra": "mean: 767.4171250691435 nsec\nrounds: 193424"
          },
          {
            "name": "tests/benchmarks/static/test_bench_discovery.py::TestBenchPathResolver::test_logical_name_for_layout_deep",
            "value": 1346072.864224729,
            "unit": "iter/sec",
            "range": "stddev: 1.0857578763563945e-7",
            "extra": "mean: 742.9018343490271 nsec\nrounds: 172385"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_backends.py::TestBenchFileRouter::test_filerouter_generate_small",
            "value": 154.19715920161937,
            "unit": "iter/sec",
            "range": "stddev: 0.00021879051852987977",
            "extra": "mean: 6.485203781818427 msec\nrounds: 55"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_backends.py::TestBenchFileRouter::test_filerouter_generate_medium",
            "value": 34.9329885444928,
            "unit": "iter/sec",
            "range": "stddev: 0.0007653597993412707",
            "extra": "mean: 28.626236736840838 msec\nrounds: 19"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_backends.py::TestBenchFileRouter::test_filerouter_generate_large",
            "value": 2.8685060191990015,
            "unit": "iter/sec",
            "range": "stddev: 0.03182659300329447",
            "extra": "mean: 348.61352680000266 msec\nrounds: 5"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_parse_simple_segment",
            "value": 1997676.665130413,
            "unit": "iter/sec",
            "range": "stddev: 7.108855459114119e-8",
            "extra": "mean: 500.5815092377413 nsec\nrounds: 167197"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_parse_typed_converter",
            "value": 306068.0918905985,
            "unit": "iter/sec",
            "range": "stddev: 6.111460152251135e-7",
            "extra": "mean: 3.267246820218822 usec\nrounds: 63212"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_prepare_url_name",
            "value": 774399.4042713351,
            "unit": "iter/sec",
            "range": "stddev: 1.8112971412449267e-7",
            "extra": "mean: 1.291323307435834 usec\nrounds: 194970"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_regex_compile_many",
            "value": 28087.93346176694,
            "unit": "iter/sec",
            "range": "stddev: 0.000003211979865485946",
            "extra": "mean: 35.60247682020436 usec\nrounds: 25561"
          }
        ]
      },
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
          "id": "4720b7ba88454bbaf6bde49a3cc30ea774627591",
          "message": "feat(core): testing module, template loaders, and rebuilt example suite (#105)\n\n* feat: added testing module with scaffold example structure\n\n* refactor(core): document signal payloads and inline JS_CONTEXT_SERIALIZER merge\n\n* refactor(core): polish conf helpers, checks, and isolation tests after review\n\n* feat(examples): added url shortener template with core features\n\n* feat(examples): added markdown blog with fixes render/template\n\n* feat(core): added TEMPLATE_LOADERS for custom page body formats\n\n* feat(examples): added feature flags admin with signals and composite render()\n\n* refactor: align examples with core and close doc/coverage gaps after review\n\n* perf(core): guard signal sends with has_listeners()\n\n* fix: speedup bench & some fixes\n\n* chore: cleanup before review\n\n* feat(tests): added more helpers for testing\n\n* chore: some fixes to docstring\n\n* fix: fixed docs & bench\n\n* fix(ci): fixed bench speedup\n\n* fix\n\n* fix",
          "timestamp": "2026-04-25T22:58:26+03:00",
          "tree_id": "8273006cf8df473a31c38aeac5c714d57f692dfd",
          "url": "https://github.com/next-dj/next-dj/commit/4720b7ba88454bbaf6bde49a3cc30ea774627591"
        },
        "date": 1777147393304,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/benchmarks/apps/test_bench_autoreload.py::TestBenchAppsAutoreload::test_install_uninstall_cycle",
            "value": 167219.16780343346,
            "unit": "iter/sec",
            "range": "stddev: 0.000009233121914403021",
            "extra": "mean: 5.980175676842875 usec\nrounds: 180832"
          },
          {
            "name": "tests/benchmarks/apps/test_bench_autoreload.py::TestBenchAppsAutoreload::test_install_idempotent",
            "value": 10724567.430872403,
            "unit": "iter/sec",
            "range": "stddev: 6.5558055933658206e-9",
            "extra": "mean: 93.24385402448382 nsec\nrounds: 114143"
          },
          {
            "name": "tests/benchmarks/components/test_bench_backends.py::TestBenchComponentScanner::test_scan_small",
            "value": 4284.0416988265715,
            "unit": "iter/sec",
            "range": "stddev: 0.000006069573478361535",
            "extra": "mean: 233.4244319503022 usec\nrounds: 4482"
          },
          {
            "name": "tests/benchmarks/components/test_bench_backends.py::TestBenchComponentScanner::test_scan_large",
            "value": 92.15708403289125,
            "unit": "iter/sec",
            "range": "stddev: 0.0000727788599407955",
            "extra": "mean: 10.851037774188862 msec\nrounds: 93"
          },
          {
            "name": "tests/benchmarks/components/test_bench_facade.py::TestBenchComponentRenderedSignal::test_send_no_receiver",
            "value": 2849463.6143509815,
            "unit": "iter/sec",
            "range": "stddev: 4.4270953364548834e-8",
            "extra": "mean: 350.94324242766953 nsec\nrounds: 193499"
          },
          {
            "name": "tests/benchmarks/components/test_bench_facade.py::TestBenchComponentRenderedSignal::test_send_with_one_receiver",
            "value": 606503.4969677706,
            "unit": "iter/sec",
            "range": "stddev: 1.0985754956269033e-7",
            "extra": "mean: 1.648795110002704 usec\nrounds: 63497"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentRegistry::test_register_bulk",
            "value": 11518.399122198716,
            "unit": "iter/sec",
            "range": "stddev: 0.000002795701745864681",
            "extra": "mean: 86.81762017368892 usec\nrounds: 11629"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentRegistry::test_lookup_by_name_hit",
            "value": 10161235.654162398,
            "unit": "iter/sec",
            "range": "stddev: 6.749019032836736e-9",
            "extra": "mean: 98.41322788241459 nsec\nrounds: 103681"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentRegistry::test_lookup_miss",
            "value": 10643267.22296564,
            "unit": "iter/sec",
            "range": "stddev: 7.0654202263922784e-9",
            "extra": "mean: 93.95611131911053 nsec\nrounds: 108803"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentVisibility::test_visibility_resolve_cold",
            "value": 2753.353663960023,
            "unit": "iter/sec",
            "range": "stddev: 0.000007378361695556217",
            "extra": "mean: 363.19344408583737 usec\nrounds: 2790"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentVisibility::test_visibility_resolve_cached",
            "value": 1833740.547889287,
            "unit": "iter/sec",
            "range": "stddev: 4.741717727867223e-8",
            "extra": "mean: 545.3334176151814 nsec\nrounds: 188254"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentVisibility::test_version_bump_invalidation",
            "value": 428.56982521844844,
            "unit": "iter/sec",
            "range": "stddev: 0.0005090250469159579",
            "extra": "mean: 2.3333420627322163 msec\nrounds: 2965"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_helpers.py::TestBenchExtendDefaultBackend::test_extend_single_override",
            "value": 168037.18188647836,
            "unit": "iter/sec",
            "range": "stddev: 6.520997663730109e-7",
            "extra": "mean: 5.951063858447558 usec\nrounds: 179663"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_helpers.py::TestBenchExtendDefaultBackend::test_extend_nested_options_merge",
            "value": 154516.62310408786,
            "unit": "iter/sec",
            "range": "stddev: 7.247891329461127e-7",
            "extra": "mean: 6.471795590085895 usec\nrounds: 163720"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_merge_cold",
            "value": 45133.526728755656,
            "unit": "iter/sec",
            "range": "stddev: 0.000001281698276070916",
            "extra": "mean: 22.156478176629523 usec\nrounds: 46991"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_merge_warm_cached",
            "value": 11865355.238458965,
            "unit": "iter/sec",
            "range": "stddev: 7.867214335503483e-9",
            "extra": "mean: 84.27897689558571 nsec\nrounds: 118414"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_attribute_access_cached",
            "value": 5955024.391646107,
            "unit": "iter/sec",
            "range": "stddev: 9.439300437641065e-9",
            "extra": "mean: 167.92542468891162 nsec\nrounds: 60621"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_reload_cycle",
            "value": 11112.32340064132,
            "unit": "iter/sec",
            "range": "stddev: 0.0000036910905733106685",
            "extra": "mean: 89.99018152606028 usec\nrounds: 11519"
          },
          {
            "name": "tests/benchmarks/deps/test_bench_resolver.py::TestBenchDependencyResolver::test_resolve_simple",
            "value": 63101.17682089876,
            "unit": "iter/sec",
            "range": "stddev: 0.0000015080845416470448",
            "extra": "mean: 15.847564980258902 usec\nrounds: 67459"
          },
          {
            "name": "tests/benchmarks/deps/test_bench_resolver.py::TestBenchDependencyResolver::test_resolve_five_params",
            "value": 38920.67553678086,
            "unit": "iter/sec",
            "range": "stddev: 0.000003389865681054075",
            "extra": "mean: 25.69328476981287 usec\nrounds: 40840"
          },
          {
            "name": "tests/benchmarks/deps/test_bench_resolver.py::TestBenchDependencyResolver::test_resolve_mixed_markers",
            "value": 39192.37426613266,
            "unit": "iter/sec",
            "range": "stddev: 0.0000015431280508565013",
            "extra": "mean: 25.515167649950996 usec\nrounds: 42040"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_register_bulk",
            "value": 4742.442650902305,
            "unit": "iter/sec",
            "range": "stddev: 0.000005026999798229052",
            "extra": "mean: 210.86180131450578 usec\nrounds: 4872"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_register_bulk_with_receiver",
            "value": 2619.343787606619,
            "unit": "iter/sec",
            "range": "stddev: 0.000005720203346571333",
            "extra": "mean: 381.7750097301023 usec\nrounds: 2672"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_get_meta_hit",
            "value": 7459587.2393167475,
            "unit": "iter/sec",
            "range": "stddev: 9.238333608435413e-9",
            "extra": "mean: 134.05567465306484 nsec\nrounds: 75839"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_get_meta_miss",
            "value": 7857254.100850135,
            "unit": "iter/sec",
            "range": "stddev: 7.406810236473081e-9",
            "extra": "mean: 127.27092533405563 nsec\nrounds: 79366"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_generate_urls_with_actions",
            "value": 236922.45055693644,
            "unit": "iter/sec",
            "range": "stddev: 3.310866330596233e-7",
            "extra": "mean: 4.220790379507252 usec\nrounds: 122011"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_none",
            "value": 15208013.098917482,
            "unit": "iter/sec",
            "range": "stddev: 5.280949622414626e-9",
            "extra": "mean: 65.75480922430167 nsec\nrounds: 161839"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_httpresponse",
            "value": 8655082.627477475,
            "unit": "iter/sec",
            "range": "stddev: 1.0781955260008293e-8",
            "extra": "mean: 115.53904717504128 nsec\nrounds: 89985"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_str",
            "value": 7060804.413623138,
            "unit": "iter/sec",
            "range": "stddev: 8.453813356482517e-9",
            "extra": "mean: 141.62692257423205 nsec\nrounds: 71979"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_redirect_duck",
            "value": 180710.2093004013,
            "unit": "iter/sec",
            "range": "stddev: 6.122723551786468e-7",
            "extra": "mean: 5.533721663382409 usec\nrounds: 188502"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_filter_reserved_url_kwargs",
            "value": 536946.2207160647,
            "unit": "iter/sec",
            "range": "stddev: 1.0883989765992022e-7",
            "extra": "mean: 1.8623839062809915 usec\nrounds: 55705"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_url_kwargs_from_post",
            "value": 109788.86761909445,
            "unit": "iter/sec",
            "range": "stddev: 6.010210954987502e-7",
            "extra": "mean: 9.108391603686423 usec\nrounds: 112310"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchEndToEnd::test_dispatch_valid_form",
            "value": 5179.052101318125,
            "unit": "iter/sec",
            "range": "stddev: 0.000012453657591967955",
            "extra": "mean: 193.0855261613393 usec\nrounds: 5466"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchEndToEnd::test_dispatch_invalid_form",
            "value": 5237.43700475441,
            "unit": "iter/sec",
            "range": "stddev: 0.000012074940939469093",
            "extra": "mean: 190.93308408143636 usec\nrounds: 5792"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchPythonModuleLoader::test_python_load_cold",
            "value": 9742.814975872401,
            "unit": "iter/sec",
            "range": "stddev: 0.000011671913490499891",
            "extra": "mean: 102.63974041141606 usec\nrounds: 10690"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchPythonModuleLoader::test_python_load_warm_mtime_hit",
            "value": 547682.2086534117,
            "unit": "iter/sec",
            "range": "stddev: 9.314128018348249e-8",
            "extra": "mean: 1.8258763644316724 usec\nrounds: 56745"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchPythonModuleLoader::test_python_template_loader_can_load",
            "value": 505164.07110182097,
            "unit": "iter/sec",
            "range": "stddev: 9.832377518614115e-8",
            "extra": "mean: 1.9795548757434094 usec\nrounds: 51141"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchDjxLoader::test_djx_can_load_hit",
            "value": 113505.49388558486,
            "unit": "iter/sec",
            "range": "stddev: 7.997023775371284e-7",
            "extra": "mean: 8.81014623845445 usec\nrounds: 116946"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchDjxLoader::test_djx_can_load_miss",
            "value": 112448.49233101412,
            "unit": "iter/sec",
            "range": "stddev: 0.000002522756160338086",
            "extra": "mean: 8.892960494804186 usec\nrounds: 117124"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchDjxLoader::test_djx_load_template",
            "value": 58019.42724716452,
            "unit": "iter/sec",
            "range": "stddev: 0.0000013278347316399898",
            "extra": "mean: 17.235606200315797 usec\nrounds: 61125"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLayoutLoader::test_ancestor_walk_no_layouts",
            "value": 5700.685462707758,
            "unit": "iter/sec",
            "range": "stddev: 0.000006648092312688622",
            "extra": "mean: 175.41750137623131 usec\nrounds: 5812"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLayoutLoader::test_ancestor_walk_with_layouts",
            "value": 5677.907267705311,
            "unit": "iter/sec",
            "range": "stddev: 0.00000661460180620502",
            "extra": "mean: 176.12122791926882 usec\nrounds: 5831"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLoaderChain::test_build_registered_loaders_warm",
            "value": 14325770.916757185,
            "unit": "iter/sec",
            "range": "stddev: 7.967744366229895e-9",
            "extra": "mean: 69.8042713240847 nsec\nrounds: 158932"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLoaderChain::test_chain_first_hit_wins",
            "value": 37873.54821220244,
            "unit": "iter/sec",
            "range": "stddev: 0.0000016164074926469514",
            "extra": "mean: 26.403652343241795 usec\nrounds: 38817"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLoaderChain::test_chain_miss_then_hit",
            "value": 110130.11682661914,
            "unit": "iter/sec",
            "range": "stddev: 7.142276574536312e-7",
            "extra": "mean: 9.080168339186704 usec\nrounds: 113200"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchComposeLayoutHierarchy::test_compose_depth_3",
            "value": 34505.813326600786,
            "unit": "iter/sec",
            "range": "stddev: 0.000001505320422587522",
            "extra": "mean: 28.980623946895715 usec\nrounds: 35136"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchComposeLayoutHierarchy::test_compose_depth_10",
            "value": 10456.04784354423,
            "unit": "iter/sec",
            "range": "stddev: 0.000004157499121579418",
            "extra": "mean: 95.63843002281399 usec\nrounds: 10632"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_manager.py::TestBenchPageRender::test_render_simple",
            "value": 4387.203094687289,
            "unit": "iter/sec",
            "range": "stddev: 0.000007504725236400064",
            "extra": "mean: 227.93565249143725 usec\nrounds: 4515"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_manager.py::TestBenchPageRender::test_render_heavy_context",
            "value": 1322.323864600959,
            "unit": "iter/sec",
            "range": "stddev: 0.000011143920391581353",
            "extra": "mean: 756.2443866970309 usec\nrounds: 1368"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_manager.py::TestBenchPageRender::test_build_render_context",
            "value": 9750.759626644773,
            "unit": "iter/sec",
            "range": "stddev: 0.0000036808813694003284",
            "extra": "mean: 102.55611237379041 usec\nrounds: 10207"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_registry.py::TestBenchPageContextRegistry::test_register_context",
            "value": 69607.91901546853,
            "unit": "iter/sec",
            "range": "stddev: 0.0000011315769300542856",
            "extra": "mean: 14.366181522791628 usec\nrounds: 71104"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_registry.py::TestBenchPageContextRegistry::test_collect_context_single",
            "value": 13009.317646164278,
            "unit": "iter/sec",
            "range": "stddev: 0.0000033738096789443413",
            "extra": "mean: 76.86798241065658 usec\nrounds: 13531"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_registry.py::TestBenchPageContextRegistry::test_collect_context_keyed_many",
            "value": 4284.044020880227,
            "unit": "iter/sec",
            "range": "stddev: 0.000007281499522666084",
            "extra": "mean: 233.42430542871347 usec\nrounds: 4384"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_render_context.py::TestBenchBuildRenderContext::test_build_small_context",
            "value": 5374.994713907737,
            "unit": "iter/sec",
            "range": "stddev: 0.000014955933158215706",
            "extra": "mean: 186.04669459720796 usec\nrounds: 5609"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_render_context.py::TestBenchBuildRenderContext::test_build_large_context",
            "value": 2743.60688297954,
            "unit": "iter/sec",
            "range": "stddev: 0.000014385067864940605",
            "extra": "mean: 364.48370435417706 usec\nrounds: 2848"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_render_context.py::TestBenchPageRenderedSignal::test_render_no_receiver",
            "value": 4423.11451221663,
            "unit": "iter/sec",
            "range": "stddev: 0.0000065517409793810555",
            "extra": "mean: 226.0850351574672 usec\nrounds: 4551"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_render_context.py::TestBenchPageRenderedSignal::test_render_with_receiver",
            "value": 4307.503482184192,
            "unit": "iter/sec",
            "range": "stddev: 0.000008357892586159493",
            "extra": "mean: 232.15303345336665 usec\nrounds: 4454"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_render_context.py::TestBenchPageRenderedSignal::test_render_with_receiver_large_context",
            "value": 1979.8688168299282,
            "unit": "iter/sec",
            "range": "stddev: 0.000012137234645979424",
            "extra": "mean: 505.08396894757533 usec\nrounds: 2061"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchTreeSignature::test_signature_small",
            "value": 4185.026055301757,
            "unit": "iter/sec",
            "range": "stddev: 0.000013636743423671982",
            "extra": "mean: 238.9471383895353 usec\nrounds: 4285"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchTreeSignature::test_signature_large",
            "value": 214.00741674811027,
            "unit": "iter/sec",
            "range": "stddev: 0.000028063567417793146",
            "extra": "mean: 4.672735249998433 msec\nrounds: 216"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchCollectRoutes::test_collect_routes_cached",
            "value": 2007.9824795155353,
            "unit": "iter/sec",
            "range": "stddev: 0.000006936941103947093",
            "extra": "mean: 498.0123134546819 usec\nrounds: 2029"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchCollectRoutes::test_collect_routes_fresh",
            "value": 214.46496216665471,
            "unit": "iter/sec",
            "range": "stddev: 0.000030437760100473333",
            "extra": "mean: 4.6627663087592275 msec\nrounds: 217"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_unique_urls",
            "value": 35069.48678879424,
            "unit": "iter/sec",
            "range": "stddev: 0.0000018703167204760014",
            "extra": "mean: 28.514817055136664 usec\nrounds: 35989"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_dedup_hit",
            "value": 60397.059109593996,
            "unit": "iter/sec",
            "range": "stddev: 9.386030673458836e-7",
            "extra": "mean: 16.557097559757693 usec\nrounds: 61962"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_inline_unique",
            "value": 31343.52895140209,
            "unit": "iter/sec",
            "range": "stddev: 0.0000014006860672447768",
            "extra": "mean: 31.904512141899932 usec\nrounds: 32038"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_js_context_many",
            "value": 3899.830236361737,
            "unit": "iter/sec",
            "range": "stddev: 0.000010610008295261222",
            "extra": "mean: 256.42141821356006 usec\nrounds: 3986"
          },
          {
            "name": "tests/benchmarks/static/test_bench_discovery.py::TestBenchPathResolver::test_find_page_root_hit_cached",
            "value": 6248633.841517576,
            "unit": "iter/sec",
            "range": "stddev: 8.057763654555188e-9",
            "extra": "mean: 160.03498130355078 nsec\nrounds: 63911"
          },
          {
            "name": "tests/benchmarks/static/test_bench_discovery.py::TestBenchPathResolver::test_logical_name_for_template_deep",
            "value": 1374730.652691265,
            "unit": "iter/sec",
            "range": "stddev: 5.724793828958395e-8",
            "extra": "mean: 727.4152198776777 nsec\nrounds: 142695"
          },
          {
            "name": "tests/benchmarks/static/test_bench_discovery.py::TestBenchPathResolver::test_logical_name_for_layout_deep",
            "value": 1300300.1789485973,
            "unit": "iter/sec",
            "range": "stddev: 5.873612331352346e-8",
            "extra": "mean: 769.0531895554952 nsec\nrounds: 133263"
          },
          {
            "name": "tests/benchmarks/static/test_bench_serializers.py::TestBenchResolveSerializer::test_resolve_default",
            "value": 5081196.931445006,
            "unit": "iter/sec",
            "range": "stddev: 1.0629035588159606e-8",
            "extra": "mean: 196.80402344012612 nsec\nrounds: 51451"
          },
          {
            "name": "tests/benchmarks/static/test_bench_serializers.py::TestBenchJsonJsContextSerializer::test_dumps_small_dict",
            "value": 443751.82931156695,
            "unit": "iter/sec",
            "range": "stddev: 1.110953342654064e-7",
            "extra": "mean: 2.253511836900801 usec\nrounds: 45223"
          },
          {
            "name": "tests/benchmarks/static/test_bench_serializers.py::TestBenchJsonJsContextSerializer::test_dumps_wide_dict",
            "value": 218457.56045636447,
            "unit": "iter/sec",
            "range": "stddev: 3.5508455235161187e-7",
            "extra": "mean: 4.577548142124125 usec\nrounds: 112033"
          },
          {
            "name": "tests/benchmarks/static/test_bench_serializers.py::TestBenchJsonJsContextSerializer::test_dumps_nested_dict",
            "value": 183799.53293386425,
            "unit": "iter/sec",
            "range": "stddev: 5.025399535591972e-7",
            "extra": "mean: 5.440710234883053 usec\nrounds: 190368"
          },
          {
            "name": "tests/benchmarks/static/test_bench_serializers.py::TestBenchPydanticJsContextSerializer::test_dumps_model",
            "value": 233428.83583653613,
            "unit": "iter/sec",
            "range": "stddev: 3.274656512329494e-7",
            "extra": "mean: 4.283960875768892 usec\nrounds: 119261"
          },
          {
            "name": "tests/benchmarks/templatetags/test_bench_template_tags.py::TestBenchStaticTags::test_use_script_dedup",
            "value": 62745.88843208842,
            "unit": "iter/sec",
            "range": "stddev: 0.0000010650098330775064",
            "extra": "mean: 15.937299239651809 usec\nrounds: 65232"
          },
          {
            "name": "tests/benchmarks/templatetags/test_bench_template_tags.py::TestBenchStaticTags::test_inline_script_block",
            "value": 127488.74622888237,
            "unit": "iter/sec",
            "range": "stddev: 6.559880658836311e-7",
            "extra": "mean: 7.843829589512831 usec\nrounds: 132486"
          },
          {
            "name": "tests/benchmarks/templatetags/test_bench_template_tags.py::TestBenchStaticTags::test_collect_placeholders",
            "value": 118307.62383372379,
            "unit": "iter/sec",
            "range": "stddev: 6.398482701735059e-7",
            "extra": "mean: 8.452540652878435 usec\nrounds: 123595"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_backends.py::TestBenchFileRouter::test_filerouter_generate_small",
            "value": 666.494134010704,
            "unit": "iter/sec",
            "range": "stddev: 0.000017364572220740164",
            "extra": "mean: 1.5003882989672943 msec\nrounds: 679"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_backends.py::TestBenchFileRouter::test_filerouter_generate_medium",
            "value": 164.8774454248694,
            "unit": "iter/sec",
            "range": "stddev: 0.000060616567006585046",
            "extra": "mean: 6.065110952096086 msec\nrounds: 167"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_backends.py::TestBenchFileRouter::test_filerouter_generate_large",
            "value": 14.168851072925392,
            "unit": "iter/sec",
            "range": "stddev: 0.0003162750664806742",
            "extra": "mean: 70.5773527333387 msec\nrounds: 15"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_parse_simple_segment",
            "value": 1860060.2185145216,
            "unit": "iter/sec",
            "range": "stddev: 5.970909587358283e-8",
            "extra": "mean: 537.617002958441 nsec\nrounds: 195313"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_parse_typed_converter",
            "value": 305021.2506484136,
            "unit": "iter/sec",
            "range": "stddev: 2.69817459095646e-7",
            "extra": "mean: 3.2784601003182625 usec\nrounds: 156104"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_prepare_url_name",
            "value": 733810.3004429069,
            "unit": "iter/sec",
            "range": "stddev: 1.0370730419706504e-7",
            "extra": "mean: 1.3627500178130896 usec\nrounds: 75735"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_regex_compile_many",
            "value": 28145.111562474183,
            "unit": "iter/sec",
            "range": "stddev: 0.0000013770142257284824",
            "extra": "mean: 35.53014873578607 usec\nrounds: 28917"
          }
        ]
      },
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
          "id": "59deed406bb8f99d0f9073424c93477459f626b7",
          "message": "feat(core): pluggable DEFAULT_FORM_ACTION_BACKENDS and audit-forms example (#106)\n\n* feat: pluggable DEFAULT_FORM_ACTION_BACKENDS and audit-forms example\n\n* fix(bench): refactored bench tests\n\n* fix: fixes bench & audit example",
          "timestamp": "2026-04-28T15:07:37+03:00",
          "tree_id": "3558bc469d18f86dcfbb5930b18c3194bca9d2a4",
          "url": "https://github.com/next-dj/next-dj/commit/59deed406bb8f99d0f9073424c93477459f626b7"
        },
        "date": 1777378416957,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/benchmarks/apps/test_bench_autoreload.py::TestBenchAppsAutoreload::test_install_uninstall_cycle",
            "value": 154854.6039173943,
            "unit": "iter/sec",
            "range": "stddev: 0.000005255152102787439",
            "extra": "mean: 6.457670451525228 usec\nrounds: 168039"
          },
          {
            "name": "tests/benchmarks/apps/test_bench_autoreload.py::TestBenchAppsAutoreload::test_install_idempotent",
            "value": 9398068.561211139,
            "unit": "iter/sec",
            "range": "stddev: 9.979658536834738e-9",
            "extra": "mean: 106.40484196160502 nsec\nrounds: 95420"
          },
          {
            "name": "tests/benchmarks/components/test_bench_backends.py::TestBenchComponentScanner::test_scan_small",
            "value": 3082.732599477919,
            "unit": "iter/sec",
            "range": "stddev: 0.000018882912219294744",
            "extra": "mean: 324.38752558991223 usec\nrounds: 3263"
          },
          {
            "name": "tests/benchmarks/components/test_bench_backends.py::TestBenchComponentScanner::test_scan_large",
            "value": 66.4789566195869,
            "unit": "iter/sec",
            "range": "stddev: 0.00019012028918608667",
            "extra": "mean: 15.042354014704362 msec\nrounds: 68"
          },
          {
            "name": "tests/benchmarks/components/test_bench_facade.py::TestBenchComponentRenderedSignal::test_send_no_receiver",
            "value": 2810452.8928604745,
            "unit": "iter/sec",
            "range": "stddev: 4.96064154221374e-8",
            "extra": "mean: 355.81453883832995 nsec\nrounds: 192716"
          },
          {
            "name": "tests/benchmarks/components/test_bench_facade.py::TestBenchComponentRenderedSignal::test_send_with_one_receiver",
            "value": 588057.370612079,
            "unit": "iter/sec",
            "range": "stddev: 1.879592752338893e-7",
            "extra": "mean: 1.7005143545078785 usec\nrounds: 60420"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentRegistry::test_register_bulk",
            "value": 9836.628532314493,
            "unit": "iter/sec",
            "range": "stddev: 0.0000061292301093923765",
            "extra": "mean: 101.66084819761987 usec\nrounds: 10237"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentRegistry::test_lookup_by_name_hit",
            "value": 8666626.170045424,
            "unit": "iter/sec",
            "range": "stddev: 1.0334707145913112e-8",
            "extra": "mean: 115.38515454333466 nsec\nrounds: 91075"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentRegistry::test_lookup_miss",
            "value": 9183535.161289712,
            "unit": "iter/sec",
            "range": "stddev: 1.0532752926474527e-8",
            "extra": "mean: 108.89052880367723 nsec\nrounds: 95612"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentVisibility::test_visibility_resolve_cold",
            "value": 2327.2043306567257,
            "unit": "iter/sec",
            "range": "stddev: 0.00001546918237420147",
            "extra": "mean: 429.7001285305295 usec\nrounds: 2443"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentVisibility::test_visibility_resolve_cached",
            "value": 1762077.218427317,
            "unit": "iter/sec",
            "range": "stddev: 8.295514384592122e-8",
            "extra": "mean: 567.5120190774139 nsec\nrounds: 181786"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentVisibility::test_version_bump_invalidation",
            "value": 408.7879443935451,
            "unit": "iter/sec",
            "range": "stddev: 0.000493821678927514",
            "extra": "mean: 2.4462560936906885 msec\nrounds: 2615"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_helpers.py::TestBenchExtendDefaultBackend::test_extend_single_override",
            "value": 161156.53756036446,
            "unit": "iter/sec",
            "range": "stddev: 9.431475099395745e-7",
            "extra": "mean: 6.205146965418201 usec\nrounds: 172088"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_helpers.py::TestBenchExtendDefaultBackend::test_extend_nested_options_merge",
            "value": 143788.63477701854,
            "unit": "iter/sec",
            "range": "stddev: 0.0000010807068284672143",
            "extra": "mean: 6.954652581205452 usec\nrounds: 154036"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_merge_cold",
            "value": 35583.2174506963,
            "unit": "iter/sec",
            "range": "stddev: 0.0000026256691063284833",
            "extra": "mean: 28.10313601870288 usec\nrounds: 38083"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_merge_warm_cached",
            "value": 9636524.16703729,
            "unit": "iter/sec",
            "range": "stddev: 9.827259786153856e-9",
            "extra": "mean: 103.77185618654926 nsec\nrounds: 97571"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_attribute_access_cached",
            "value": 5647986.722377887,
            "unit": "iter/sec",
            "range": "stddev: 3.5765432301960696e-8",
            "extra": "mean: 177.05424059123587 nsec\nrounds: 58032"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_reload_cycle",
            "value": 8765.72088217544,
            "unit": "iter/sec",
            "range": "stddev: 0.000009389104555492044",
            "extra": "mean: 114.0807485706554 usec\nrounds: 9271"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_merge_with_user_form_action_backends",
            "value": 38481.649035129376,
            "unit": "iter/sec",
            "range": "stddev: 0.00000219765829864119",
            "extra": "mean: 25.986412356890256 usec\nrounds: 39735"
          },
          {
            "name": "tests/benchmarks/deps/test_bench_resolver.py::TestBenchDependencyResolver::test_resolve_simple",
            "value": 57926.50546388093,
            "unit": "iter/sec",
            "range": "stddev: 0.000002208389012371958",
            "extra": "mean: 17.263254394372755 usec\nrounds: 57631"
          },
          {
            "name": "tests/benchmarks/deps/test_bench_resolver.py::TestBenchDependencyResolver::test_resolve_five_params",
            "value": 35334.84981115747,
            "unit": "iter/sec",
            "range": "stddev: 0.0000028186384136069642",
            "extra": "mean: 28.300672150705903 usec\nrounds: 35983"
          },
          {
            "name": "tests/benchmarks/deps/test_bench_resolver.py::TestBenchDependencyResolver::test_resolve_mixed_markers",
            "value": 37050.78075417003,
            "unit": "iter/sec",
            "range": "stddev: 0.0000030543301614599395",
            "extra": "mean: 26.98998454674807 usec\nrounds: 37468"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_register_bulk",
            "value": 4800.543221390978,
            "unit": "iter/sec",
            "range": "stddev: 0.000019911843397320522",
            "extra": "mean: 208.3097586839861 usec\nrounds: 4894"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_register_bulk_with_receiver",
            "value": 2599.110032692291,
            "unit": "iter/sec",
            "range": "stddev: 0.000010300636232788286",
            "extra": "mean: 384.7470816632372 usec\nrounds: 2645"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_get_meta_hit",
            "value": 6468313.538368972,
            "unit": "iter/sec",
            "range": "stddev: 1.1927640846469583e-8",
            "extra": "mean: 154.59980318952762 nsec\nrounds: 66765"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_get_meta_miss",
            "value": 7075731.208364239,
            "unit": "iter/sec",
            "range": "stddev: 2.205102390043035e-8",
            "extra": "mean: 141.32814977735413 nsec\nrounds: 72067"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_generate_urls_with_actions",
            "value": 212434.11513181656,
            "unit": "iter/sec",
            "range": "stddev: 6.887672462098128e-7",
            "extra": "mean: 4.707341847515849 usec\nrounds: 108838"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_checks.py::TestBenchFormActionBackendsCheck::test_check_clean",
            "value": 318821.1031943545,
            "unit": "iter/sec",
            "range": "stddev: 9.396888206568893e-7",
            "extra": "mean: 3.136555234207305 usec\nrounds: 168039"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_checks.py::TestBenchFormActionBackendsCheck::test_check_e044_unimportable",
            "value": 10333.518862714642,
            "unit": "iter/sec",
            "range": "stddev: 0.000007398103503387258",
            "extra": "mean: 96.77245605155817 usec\nrounds: 10683"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_checks.py::TestBenchFormActionBackendsCheck::test_check_e045_wrong_subclass",
            "value": 321849.8759162801,
            "unit": "iter/sec",
            "range": "stddev: 4.781723816861939e-7",
            "extra": "mean: 3.1070386376663413 usec\nrounds: 166612"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_none",
            "value": 13282202.622027354,
            "unit": "iter/sec",
            "range": "stddev: 1.2657941040402334e-8",
            "extra": "mean: 75.28871742564661 nsec\nrounds: 136725"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_httpresponse",
            "value": 7584392.904619325,
            "unit": "iter/sec",
            "range": "stddev: 1.1240576900196618e-8",
            "extra": "mean: 131.8497093407362 nsec\nrounds: 77376"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_str",
            "value": 6290311.070769154,
            "unit": "iter/sec",
            "range": "stddev: 1.2874010129364268e-8",
            "extra": "mean: 158.9746498622244 nsec\nrounds: 67445"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_redirect_duck",
            "value": 160305.17108151712,
            "unit": "iter/sec",
            "range": "stddev: 0.000001623933643758995",
            "extra": "mean: 6.238101948011944 usec\nrounds: 165810"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_filter_reserved_url_kwargs",
            "value": 532272.389166081,
            "unit": "iter/sec",
            "range": "stddev: 2.316738690956695e-7",
            "extra": "mean: 1.8787373163705048 usec\nrounds: 53778"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_url_kwargs_from_post",
            "value": 125854.78792612952,
            "unit": "iter/sec",
            "range": "stddev: 8.862424113738404e-7",
            "extra": "mean: 7.9456651310473 usec\nrounds: 129803"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchEndToEnd::test_dispatch_valid_form",
            "value": 4156.591700827056,
            "unit": "iter/sec",
            "range": "stddev: 0.000018408515155733868",
            "extra": "mean: 240.5817246377664 usec\nrounds: 4485"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchEndToEnd::test_dispatch_invalid_form",
            "value": 4236.898550232594,
            "unit": "iter/sec",
            "range": "stddev: 0.000014116301004190213",
            "extra": "mean: 236.02170034142137 usec\nrounds: 4979"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchEndToEnd::test_dispatch_through_subclassed_backend",
            "value": 4349.019650845575,
            "unit": "iter/sec",
            "range": "stddev: 0.000019276770384765912",
            "extra": "mean: 229.9368777985566 usec\nrounds: 4869"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_factory.py::TestBenchFormActionFactory::test_create_backend_cached",
            "value": 2567108.174016262,
            "unit": "iter/sec",
            "range": "stddev: 5.621259334170108e-8",
            "extra": "mean: 389.5433819742359 nsec\nrounds: 188006"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_factory.py::TestBenchFormActionFactory::test_create_backend_cold",
            "value": 914106.0283968109,
            "unit": "iter/sec",
            "range": "stddev: 3.6230614495953127e-7",
            "extra": "mean: 1.0939649985175492 usec\nrounds: 200"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchEnsureBackends::test_ensure_backends_warm",
            "value": 10029433.042931538,
            "unit": "iter/sec",
            "range": "stddev: 9.91841727359522e-9",
            "extra": "mean: 99.70653333238731 nsec\nrounds: 100624"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchEnsureBackends::test_reload_config_cold",
            "value": 1162992.7937568875,
            "unit": "iter/sec",
            "range": "stddev: 1.0302274280825846e-7",
            "extra": "mean: 859.8505557112164 nsec\nrounds: 121286"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchRegisterThroughManager::test_register_bulk_via_manager",
            "value": 4462.992562301245,
            "unit": "iter/sec",
            "range": "stddev: 0.000008195551115143293",
            "extra": "mean: 224.0649039944561 usec\nrounds: 4531"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchManagerLookups::test_meta_lookup_through_manager",
            "value": 4972043.153176482,
            "unit": "iter/sec",
            "range": "stddev: 1.4595169326148243e-8",
            "extra": "mean: 201.12456171285072 nsec\nrounds: 52785"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchManagerLookups::test_get_action_url_miss",
            "value": 1558415.8350117018,
            "unit": "iter/sec",
            "range": "stddev: 7.827959304101356e-8",
            "extra": "mean: 641.6772581064612 nsec\nrounds: 160721"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchManagerLookups::test_default_backend_property",
            "value": 7309279.1313668,
            "unit": "iter/sec",
            "range": "stddev: 2.0628477043604112e-8",
            "extra": "mean: 136.81239723198325 nsec\nrounds: 75558"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchManagerLookups::test_iter_url_patterns",
            "value": 190140.60915241748,
            "unit": "iter/sec",
            "range": "stddev: 8.984987893026103e-7",
            "extra": "mean: 5.259265784714068 usec\nrounds: 196851"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchClearRegistries::test_clear_registries",
            "value": 256056.3733003564,
            "unit": "iter/sec",
            "range": "stddev: 5.719411510533011e-7",
            "extra": "mean: 3.905390001079922 usec\nrounds: 200"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchPythonModuleLoader::test_python_load_cold",
            "value": 10945.685319656983,
            "unit": "iter/sec",
            "range": "stddev: 0.000016451447933432735",
            "extra": "mean: 91.36020000539702 usec\nrounds: 20"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchPythonModuleLoader::test_python_load_warm_mtime_hit",
            "value": 334323.38297858613,
            "unit": "iter/sec",
            "range": "stddev: 4.918815084512158e-7",
            "extra": "mean: 2.991115940173563 usec\nrounds: 171498"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchPythonModuleLoader::test_python_template_loader_can_load",
            "value": 317985.94560301694,
            "unit": "iter/sec",
            "range": "stddev: 4.61391844236764e-7",
            "extra": "mean: 3.1447930760072946 usec\nrounds: 162840"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchDjxLoader::test_djx_can_load_hit",
            "value": 93450.08671299288,
            "unit": "iter/sec",
            "range": "stddev: 0.0000025659205398821255",
            "extra": "mean: 10.700899647864794 usec\nrounds: 97666"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchDjxLoader::test_djx_can_load_miss",
            "value": 96352.89642198653,
            "unit": "iter/sec",
            "range": "stddev: 0.0000017727666334583958",
            "extra": "mean: 10.378515199173735 usec\nrounds: 100827"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchDjxLoader::test_djx_load_template",
            "value": 41758.63319991519,
            "unit": "iter/sec",
            "range": "stddev: 0.000003524088430844629",
            "extra": "mean: 23.94714394057397 usec\nrounds: 43914"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLayoutLoader::test_ancestor_walk_no_layouts",
            "value": 4818.14077485132,
            "unit": "iter/sec",
            "range": "stddev: 0.000010193276404730042",
            "extra": "mean: 207.5489378018139 usec\nrounds: 4968"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLayoutLoader::test_ancestor_walk_with_layouts",
            "value": 4653.1495445673245,
            "unit": "iter/sec",
            "range": "stddev: 0.00002076121644936397",
            "extra": "mean: 214.90820151428971 usec\nrounds: 4888"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLoaderChain::test_build_registered_loaders_warm",
            "value": 12319584.192115549,
            "unit": "iter/sec",
            "range": "stddev: 8.502650999082359e-9",
            "extra": "mean: 81.17157076129187 nsec\nrounds: 127486"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLoaderChain::test_chain_first_hit_wins",
            "value": 26739.27400947594,
            "unit": "iter/sec",
            "range": "stddev: 0.000004627617318665382",
            "extra": "mean: 37.398173175742066 usec\nrounds: 28422"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLoaderChain::test_chain_miss_then_hit",
            "value": 92516.74962978439,
            "unit": "iter/sec",
            "range": "stddev: 0.000001619367315369412",
            "extra": "mean: 10.80885357518078 usec\nrounds: 97381"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchComposeLayoutHierarchy::test_compose[3]",
            "value": 23990.193085193587,
            "unit": "iter/sec",
            "range": "stddev: 0.000004195452088601209",
            "extra": "mean: 41.68369952041721 usec\nrounds: 24817"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchComposeLayoutHierarchy::test_compose[10]",
            "value": 7146.222779692404,
            "unit": "iter/sec",
            "range": "stddev: 0.000007338419080719802",
            "extra": "mean: 139.93406458608095 usec\nrounds: 7370"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_manager.py::TestBenchPageRender::test_render_simple",
            "value": 3482.1545648970014,
            "unit": "iter/sec",
            "range": "stddev: 0.000014409603166880053",
            "extra": "mean: 287.17852162015646 usec\nrounds: 3654"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_manager.py::TestBenchPageRender::test_render_heavy_context",
            "value": 1092.4013063770378,
            "unit": "iter/sec",
            "range": "stddev: 0.00002136109215902192",
            "extra": "mean: 915.4145039578103 usec\nrounds: 1137"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_manager.py::TestBenchPageRender::test_build_render_context",
            "value": 8213.499975407349,
            "unit": "iter/sec",
            "range": "stddev: 0.000008921680839079059",
            "extra": "mean: 121.75077652574106 usec\nrounds: 8699"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_registry.py::TestBenchPageContextRegistry::test_register_context",
            "value": 65808.74941977742,
            "unit": "iter/sec",
            "range": "stddev: 0.0000014416718722970374",
            "extra": "mean: 15.19554783849868 usec\nrounds: 67038"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_registry.py::TestBenchPageContextRegistry::test_collect_context_single",
            "value": 11362.431835982508,
            "unit": "iter/sec",
            "range": "stddev: 0.000007299924724985925",
            "extra": "mean: 88.00932885099505 usec\nrounds: 11750"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_registry.py::TestBenchPageContextRegistry::test_collect_context_keyed_many",
            "value": 3759.03923587544,
            "unit": "iter/sec",
            "range": "stddev: 0.00001208306128461409",
            "extra": "mean: 266.0254222558309 usec\nrounds: 3936"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_render_context.py::TestBenchBuildRenderContext::test_build_context[small]",
            "value": 4531.821650792525,
            "unit": "iter/sec",
            "range": "stddev: 0.000012270820295880436",
            "extra": "mean: 220.6618170477031 usec\nrounds: 4810"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_render_context.py::TestBenchBuildRenderContext::test_build_context[large]",
            "value": 2342.2736786078467,
            "unit": "iter/sec",
            "range": "stddev: 0.000015776154061982924",
            "extra": "mean: 426.9355921697245 usec\nrounds: 2452"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_render_context.py::TestBenchPageRenderedSignal::test_render_no_receiver",
            "value": 3476.9415397399944,
            "unit": "iter/sec",
            "range": "stddev: 0.000015010149342509415",
            "extra": "mean: 287.60909223535003 usec\nrounds: 3632"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_render_context.py::TestBenchPageRenderedSignal::test_render_with_receiver",
            "value": 3410.8763436321215,
            "unit": "iter/sec",
            "range": "stddev: 0.00001536332182227429",
            "extra": "mean: 293.17978702656086 usec\nrounds: 3592"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_render_context.py::TestBenchPageRenderedSignal::test_render_with_receiver_large_context",
            "value": 1610.290862812518,
            "unit": "iter/sec",
            "range": "stddev: 0.000023531747473639915",
            "extra": "mean: 621.0058214286889 usec\nrounds: 1708"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchTreeSignature::test_signature[small]",
            "value": 2261.7161698443197,
            "unit": "iter/sec",
            "range": "stddev: 0.000010197911611022781",
            "extra": "mean: 442.1421278819582 usec\nrounds: 2299"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchTreeSignature::test_signature[large]",
            "value": 113.89569535617977,
            "unit": "iter/sec",
            "range": "stddev: 0.00011174442972117397",
            "extra": "mean: 8.779963078259936 msec\nrounds: 115"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchCollectRoutes::test_collect_routes_cached",
            "value": 1064.3111003497224,
            "unit": "iter/sec",
            "range": "stddev: 0.000011429412392607576",
            "extra": "mean: 939.574904058982 usec\nrounds: 1084"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchCollectRoutes::test_collect_routes_fresh",
            "value": 145.8129591547331,
            "unit": "iter/sec",
            "range": "stddev: 0.00028371042737524056",
            "extra": "mean: 6.858100993196529 msec\nrounds: 147"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_unique_urls",
            "value": 29194.92771574118,
            "unit": "iter/sec",
            "range": "stddev: 0.000001991571071965139",
            "extra": "mean: 34.25252529263241 usec\nrounds: 30404"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_dedup_hit",
            "value": 52044.207680792,
            "unit": "iter/sec",
            "range": "stddev: 0.00000149950280020337",
            "extra": "mean: 19.21443412364736 usec\nrounds: 52591"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_inline_unique",
            "value": 26964.148540851384,
            "unit": "iter/sec",
            "range": "stddev: 0.0000022715361017717137",
            "extra": "mean: 37.08628138155277 usec\nrounds: 27827"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_js_context_many",
            "value": 3705.096858964929,
            "unit": "iter/sec",
            "range": "stddev: 0.000009617424370901077",
            "extra": "mean: 269.89847716946434 usec\nrounds: 3789"
          },
          {
            "name": "tests/benchmarks/static/test_bench_discovery.py::TestBenchPathResolver::test_find_page_root_hit_cached",
            "value": 5732154.14613991,
            "unit": "iter/sec",
            "range": "stddev: 1.8338450443127907e-8",
            "extra": "mean: 174.45448508627598 nsec\nrounds: 57631"
          },
          {
            "name": "tests/benchmarks/static/test_bench_discovery.py::TestBenchPathResolver::test_logical_name_for_template_deep",
            "value": 1405717.8242171747,
            "unit": "iter/sec",
            "range": "stddev: 9.025983789700385e-8",
            "extra": "mean: 711.3803231149087 nsec\nrounds: 145709"
          },
          {
            "name": "tests/benchmarks/static/test_bench_discovery.py::TestBenchPathResolver::test_logical_name_for_layout_deep",
            "value": 1298248.9426488688,
            "unit": "iter/sec",
            "range": "stddev: 9.717755416975118e-8",
            "extra": "mean: 770.2682953545568 nsec\nrounds: 133441"
          },
          {
            "name": "tests/benchmarks/static/test_bench_serializers.py::TestBenchResolveSerializer::test_resolve_default",
            "value": 5013658.922008625,
            "unit": "iter/sec",
            "range": "stddev: 1.5445555161002734e-8",
            "extra": "mean: 199.4551315826984 nsec\nrounds: 51185"
          },
          {
            "name": "tests/benchmarks/static/test_bench_serializers.py::TestBenchJsonJsContextSerializer::test_dumps_small_dict",
            "value": 421965.22328359936,
            "unit": "iter/sec",
            "range": "stddev: 2.856412592244832e-7",
            "extra": "mean: 2.3698635451953067 usec\nrounds: 42875"
          },
          {
            "name": "tests/benchmarks/static/test_bench_serializers.py::TestBenchJsonJsContextSerializer::test_dumps_wide_dict",
            "value": 208036.36888583945,
            "unit": "iter/sec",
            "range": "stddev: 5.956073165969569e-7",
            "extra": "mean: 4.8068518276665015 usec\nrounds: 105843"
          },
          {
            "name": "tests/benchmarks/static/test_bench_serializers.py::TestBenchJsonJsContextSerializer::test_dumps_nested_dict",
            "value": 180189.4669567015,
            "unit": "iter/sec",
            "range": "stddev: 8.667264206244615e-7",
            "extra": "mean: 5.5497139588092255 usec\nrounds: 183117"
          },
          {
            "name": "tests/benchmarks/static/test_bench_serializers.py::TestBenchPydanticJsContextSerializer::test_dumps_model",
            "value": 221077.33509297215,
            "unit": "iter/sec",
            "range": "stddev: 6.569212570089839e-7",
            "extra": "mean: 4.523304026527453 usec\nrounds: 112158"
          },
          {
            "name": "tests/benchmarks/templatetags/test_bench_template_tags.py::TestBenchStaticTags::test_use_script_dedup",
            "value": 55122.997529734595,
            "unit": "iter/sec",
            "range": "stddev: 0.0000020710169195058153",
            "extra": "mean: 18.141248567997 usec\nrounds: 56045"
          },
          {
            "name": "tests/benchmarks/templatetags/test_bench_template_tags.py::TestBenchStaticTags::test_inline_script_block",
            "value": 112889.5985884467,
            "unit": "iter/sec",
            "range": "stddev: 0.0000015453746887156147",
            "extra": "mean: 8.858212027536979 usec\nrounds: 117565"
          },
          {
            "name": "tests/benchmarks/templatetags/test_bench_template_tags.py::TestBenchStaticTags::test_collect_placeholders",
            "value": 106490.24991508003,
            "unit": "iter/sec",
            "range": "stddev: 0.00000120390767012797",
            "extra": "mean: 9.390531065496077 usec\nrounds: 110412"
          },
          {
            "name": "tests/benchmarks/testing/test_bench_isolation.py::TestBenchResetFormActions::test_reset_form_actions_only",
            "value": 329549.55520004366,
            "unit": "iter/sec",
            "range": "stddev: 0.0000010645079217325683",
            "extra": "mean: 3.0344449999120116 usec\nrounds: 200"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_backends.py::TestBenchFileRouter::test_filerouter_generate_small",
            "value": 481.8808921616766,
            "unit": "iter/sec",
            "range": "stddev: 0.00003166750080590166",
            "extra": "mean: 2.07520160327189 msec\nrounds: 489"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_backends.py::TestBenchFileRouter::test_filerouter_generate_medium",
            "value": 120.31387432247342,
            "unit": "iter/sec",
            "range": "stddev: 0.00007395548549594818",
            "extra": "mean: 8.311593368855632 msec\nrounds: 122"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_backends.py::TestBenchFileRouter::test_filerouter_generate_large",
            "value": 10.003383617226406,
            "unit": "iter/sec",
            "range": "stddev: 0.00039505244421060527",
            "extra": "mean: 99.96617527272892 msec\nrounds: 11"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_parse_simple_segment",
            "value": 1808698.2699055276,
            "unit": "iter/sec",
            "range": "stddev: 8.030745286233146e-8",
            "extra": "mean: 552.8838151939142 nsec\nrounds: 186916"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_parse_typed_converter",
            "value": 295890.0065247478,
            "unit": "iter/sec",
            "range": "stddev: 5.632559108943652e-7",
            "extra": "mean: 3.3796342490409907 usec\nrounds: 150083"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_prepare_url_name",
            "value": 765404.9927984185,
            "unit": "iter/sec",
            "range": "stddev: 1.397488218747749e-7",
            "extra": "mean: 1.3064978794348756 usec\nrounds: 76017"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_regex_compile_many",
            "value": 26616.571356807166,
            "unit": "iter/sec",
            "range": "stddev: 0.000002615512529565565",
            "extra": "mean: 37.57057911759363 usec\nrounds: 27535"
          }
        ]
      },
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
          "id": "c696ea11cf228ff2a1530995db4199b32cd44319",
          "message": "feat(core): DQuery[T] provider for query-string DI & search-catalog example (#107)\n\n* feat(examples): added search catalog example\n\n* fix: some fixes after review\n\n* fix: added search js validation\n\n* fix: cleanup readme docs\n\n* feat(docs): added docs linter",
          "timestamp": "2026-04-30T13:47:23+03:00",
          "tree_id": "4f2243d0936e0e1e39cce95137876c1308696887",
          "url": "https://github.com/next-dj/next-dj/commit/c696ea11cf228ff2a1530995db4199b32cd44319"
        },
        "date": 1777546423772,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/benchmarks/apps/test_bench_autoreload.py::TestBenchAppsAutoreload::test_install_uninstall_cycle",
            "value": 155865.73108763256,
            "unit": "iter/sec",
            "range": "stddev: 0.000005381930383148158",
            "extra": "mean: 6.4157784589466225 usec\nrounds: 169463"
          },
          {
            "name": "tests/benchmarks/apps/test_bench_autoreload.py::TestBenchAppsAutoreload::test_install_idempotent",
            "value": 9801316.109499106,
            "unit": "iter/sec",
            "range": "stddev: 9.578041601901263e-9",
            "extra": "mean: 102.02711440260902 nsec\nrounds: 100513"
          },
          {
            "name": "tests/benchmarks/components/test_bench_backends.py::TestBenchComponentScanner::test_scan_small",
            "value": 3113.715801832419,
            "unit": "iter/sec",
            "range": "stddev: 0.000013532653684556434",
            "extra": "mean: 321.1596894653972 usec\nrounds: 3275"
          },
          {
            "name": "tests/benchmarks/components/test_bench_backends.py::TestBenchComponentScanner::test_scan_large",
            "value": 68.06756348075795,
            "unit": "iter/sec",
            "range": "stddev: 0.00013208693277804375",
            "extra": "mean: 14.691285376810798 msec\nrounds: 69"
          },
          {
            "name": "tests/benchmarks/components/test_bench_facade.py::TestBenchComponentRenderedSignal::test_send_no_receiver",
            "value": 2807618.3174443645,
            "unit": "iter/sec",
            "range": "stddev: 4.662796192373383e-8",
            "extra": "mean: 356.17376969895616 nsec\nrounds: 192716"
          },
          {
            "name": "tests/benchmarks/components/test_bench_facade.py::TestBenchComponentRenderedSignal::test_send_with_one_receiver",
            "value": 583714.7412554882,
            "unit": "iter/sec",
            "range": "stddev: 1.4914428902952743e-7",
            "extra": "mean: 1.7131655744193486 usec\nrounds: 59877"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentRegistry::test_register_bulk",
            "value": 10291.481709271236,
            "unit": "iter/sec",
            "range": "stddev: 0.000003782030293673655",
            "extra": "mean: 97.1677381595242 usec\nrounds: 10430"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentRegistry::test_lookup_by_name_hit",
            "value": 8739445.57127657,
            "unit": "iter/sec",
            "range": "stddev: 9.915559528954079e-9",
            "extra": "mean: 114.42373453147212 nsec\nrounds: 90662"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentRegistry::test_lookup_miss",
            "value": 9073583.928486386,
            "unit": "iter/sec",
            "range": "stddev: 1.0296936022652077e-8",
            "extra": "mean: 110.21003474277836 nsec\nrounds: 92679"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentVisibility::test_visibility_resolve_cold",
            "value": 2376.0636269758293,
            "unit": "iter/sec",
            "range": "stddev: 0.000014478168620010954",
            "extra": "mean: 420.8641505416103 usec\nrounds: 2491"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentVisibility::test_visibility_resolve_cached",
            "value": 1774852.481104315,
            "unit": "iter/sec",
            "range": "stddev: 7.923836303074655e-8",
            "extra": "mean: 563.4271076871689 nsec\nrounds: 184468"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentVisibility::test_version_bump_invalidation",
            "value": 410.44458715305234,
            "unit": "iter/sec",
            "range": "stddev: 0.0005020761829055519",
            "extra": "mean: 2.436382477196869 msec\nrounds: 2697"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_helpers.py::TestBenchExtendDefaultBackend::test_extend_single_override",
            "value": 163540.02505002395,
            "unit": "iter/sec",
            "range": "stddev: 8.914791493340392e-7",
            "extra": "mean: 6.114711060452131 usec\nrounds: 171233"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_helpers.py::TestBenchExtendDefaultBackend::test_extend_nested_options_merge",
            "value": 149709.51024204562,
            "unit": "iter/sec",
            "range": "stddev: 0.0000011356911717093569",
            "extra": "mean: 6.679602373845399 usec\nrounds: 156446"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_merge_cold",
            "value": 37357.75583656588,
            "unit": "iter/sec",
            "range": "stddev: 0.000002309397847117415",
            "extra": "mean: 26.768203217956607 usec\nrounds: 38658"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_merge_warm_cached",
            "value": 9972976.27982744,
            "unit": "iter/sec",
            "range": "stddev: 9.742008479311435e-9",
            "extra": "mean: 100.27096946201729 nsec\nrounds: 99612"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_attribute_access_cached",
            "value": 5606925.309878034,
            "unit": "iter/sec",
            "range": "stddev: 2.045369654650434e-8",
            "extra": "mean: 178.35086874409473 nsec\nrounds: 57232"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_reload_cycle",
            "value": 8900.844115504886,
            "unit": "iter/sec",
            "range": "stddev: 0.0000071263635955814824",
            "extra": "mean: 112.34889489391723 usec\nrounds: 9381"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_merge_with_user_form_action_backends",
            "value": 39543.21531078786,
            "unit": "iter/sec",
            "range": "stddev: 0.000001994769443820646",
            "extra": "mean: 25.288788282403228 usec\nrounds: 40691"
          },
          {
            "name": "tests/benchmarks/deps/test_bench_resolver.py::TestBenchDependencyResolver::test_resolve_simple",
            "value": 56718.09897466383,
            "unit": "iter/sec",
            "range": "stddev: 0.0000022293906435698514",
            "extra": "mean: 17.631056366094068 usec\nrounds: 56488"
          },
          {
            "name": "tests/benchmarks/deps/test_bench_resolver.py::TestBenchDependencyResolver::test_resolve_five_params",
            "value": 33612.866840553004,
            "unit": "iter/sec",
            "range": "stddev: 0.0000031399271442416386",
            "extra": "mean: 29.750512050746213 usec\nrounds: 34479"
          },
          {
            "name": "tests/benchmarks/deps/test_bench_resolver.py::TestBenchDependencyResolver::test_resolve_mixed_markers",
            "value": 36933.34770393747,
            "unit": "iter/sec",
            "range": "stddev: 0.0000027823005240685227",
            "extra": "mean: 27.07580173928804 usec\nrounds: 37370"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_register_bulk",
            "value": 4881.302493936449,
            "unit": "iter/sec",
            "range": "stddev: 0.00001076253931555714",
            "extra": "mean: 204.86335383684977 usec\nrounds: 4991"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_register_bulk_with_receiver",
            "value": 2556.0585092973893,
            "unit": "iter/sec",
            "range": "stddev: 0.00004679781901317471",
            "extra": "mean: 391.2273511590627 usec\nrounds: 2674"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_get_meta_hit",
            "value": 6482720.473914575,
            "unit": "iter/sec",
            "range": "stddev: 1.3985624124681648e-8",
            "extra": "mean: 154.25622684547935 nsec\nrounds: 66944"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_get_meta_miss",
            "value": 6811337.516423302,
            "unit": "iter/sec",
            "range": "stddev: 1.4529994723084988e-8",
            "extra": "mean: 146.8140431433369 nsec\nrounds: 69028"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_generate_urls_with_actions",
            "value": 211183.71158119978,
            "unit": "iter/sec",
            "range": "stddev: 8.919755890970529e-7",
            "extra": "mean: 4.735213679656832 usec\nrounds: 110792"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_checks.py::TestBenchFormActionBackendsCheck::test_check_clean",
            "value": 324829.4414067614,
            "unit": "iter/sec",
            "range": "stddev: 4.967602252456724e-7",
            "extra": "mean: 3.078538680697262 usec\nrounds: 167758"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_checks.py::TestBenchFormActionBackendsCheck::test_check_e044_unimportable",
            "value": 10271.08665132633,
            "unit": "iter/sec",
            "range": "stddev: 0.000008105264416115274",
            "extra": "mean: 97.36068187788753 usec\nrounds: 10716"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_checks.py::TestBenchFormActionBackendsCheck::test_check_e045_wrong_subclass",
            "value": 330553.1746927388,
            "unit": "iter/sec",
            "range": "stddev: 5.106594312105164e-7",
            "extra": "mean: 3.0252318735995694 usec\nrounds: 170649"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_none",
            "value": 13473304.223039897,
            "unit": "iter/sec",
            "range": "stddev: 9.920837469016663e-9",
            "extra": "mean: 74.22084319078607 nsec\nrounds: 137288"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_httpresponse",
            "value": 8019800.856384939,
            "unit": "iter/sec",
            "range": "stddev: 1.3372124880833152e-8",
            "extra": "mean: 124.69137549766627 nsec\nrounds: 80887"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_str",
            "value": 6584724.747287062,
            "unit": "iter/sec",
            "range": "stddev: 1.430125745110159e-8",
            "extra": "mean: 151.86663655333575 nsec\nrounds: 70294"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_redirect_duck",
            "value": 160476.1583509237,
            "unit": "iter/sec",
            "range": "stddev: 0.0000010071046494712757",
            "extra": "mean: 6.231455253391813 usec\nrounds: 167197"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_filter_reserved_url_kwargs",
            "value": 542780.7226754528,
            "unit": "iter/sec",
            "range": "stddev: 1.4117444573082335e-7",
            "extra": "mean: 1.8423646202297685 usec\nrounds: 55088"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_url_kwargs_from_post",
            "value": 124366.83233108181,
            "unit": "iter/sec",
            "range": "stddev: 9.757571643075129e-7",
            "extra": "mean: 8.040729037287537 usec\nrounds: 128800"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchEndToEnd::test_dispatch_valid_form",
            "value": 4133.13718377014,
            "unit": "iter/sec",
            "range": "stddev: 0.000018933025045754794",
            "extra": "mean: 241.94696559474613 usec\nrounds: 4447"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchEndToEnd::test_dispatch_invalid_form",
            "value": 4158.882513688757,
            "unit": "iter/sec",
            "range": "stddev: 0.000014906215240968013",
            "extra": "mean: 240.44920641748092 usec\nrounds: 4893"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchEndToEnd::test_dispatch_through_subclassed_backend",
            "value": 4103.897221443269,
            "unit": "iter/sec",
            "range": "stddev: 0.000021280971526737564",
            "extra": "mean: 243.67081972104498 usec\nrounds: 4665"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_factory.py::TestBenchFormActionFactory::test_create_backend_cached",
            "value": 2550381.419896474,
            "unit": "iter/sec",
            "range": "stddev: 7.363703070293316e-8",
            "extra": "mean: 392.0982140940285 nsec\nrounds: 186916"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_factory.py::TestBenchFormActionFactory::test_create_backend_cold",
            "value": 912167.4020791746,
            "unit": "iter/sec",
            "range": "stddev: 3.7964275331336467e-7",
            "extra": "mean: 1.09628999865663 usec\nrounds: 200"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchEnsureBackends::test_ensure_backends_warm",
            "value": 10102570.538696159,
            "unit": "iter/sec",
            "range": "stddev: 1.1064460062595386e-8",
            "extra": "mean: 98.98470851251886 nsec\nrounds: 102166"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchEnsureBackends::test_reload_config_cold",
            "value": 1177336.9189633143,
            "unit": "iter/sec",
            "range": "stddev: 1.2191839809826864e-7",
            "extra": "mean: 849.3745366284228 nsec\nrounds: 122474"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchRegisterThroughManager::test_register_bulk_via_manager",
            "value": 4573.20272508891,
            "unit": "iter/sec",
            "range": "stddev: 0.000007802778746052187",
            "extra": "mean: 218.66513691027302 usec\nrounds: 4660"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchManagerLookups::test_meta_lookup_through_manager",
            "value": 5377523.259460791,
            "unit": "iter/sec",
            "range": "stddev: 1.3927222103464043e-8",
            "extra": "mean: 185.95921426107805 nsec\nrounds: 55451"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchManagerLookups::test_get_action_url_miss",
            "value": 1553640.83780761,
            "unit": "iter/sec",
            "range": "stddev: 7.972309251713395e-8",
            "extra": "mean: 643.649404460255 nsec\nrounds: 158429"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchManagerLookups::test_default_backend_property",
            "value": 7344516.172505855,
            "unit": "iter/sec",
            "range": "stddev: 1.1059915782467737e-8",
            "extra": "mean: 136.1560076269548 nsec\nrounds: 75787"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchManagerLookups::test_iter_url_patterns",
            "value": 189238.7710243064,
            "unit": "iter/sec",
            "range": "stddev: 9.492038181705711e-7",
            "extra": "mean: 5.28432939289992 usec\nrounds: 197278"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchClearRegistries::test_clear_registries",
            "value": 241629.64708047107,
            "unit": "iter/sec",
            "range": "stddev: 0.0000014035474488813803",
            "extra": "mean: 4.138564998470429 usec\nrounds: 200"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchPythonModuleLoader::test_python_load_cold",
            "value": 8725.07844936383,
            "unit": "iter/sec",
            "range": "stddev: 0.000024138930319504547",
            "extra": "mean: 114.61214999997082 usec\nrounds: 20"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchPythonModuleLoader::test_python_load_warm_mtime_hit",
            "value": 332357.38622707163,
            "unit": "iter/sec",
            "range": "stddev: 4.395228933785962e-7",
            "extra": "mean: 3.0088093162364227 usec\nrounds: 170649"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchPythonModuleLoader::test_python_template_loader_can_load",
            "value": 310200.9884929234,
            "unit": "iter/sec",
            "range": "stddev: 4.621525914241794e-7",
            "extra": "mean: 3.223716355187608 usec\nrounds: 160463"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchDjxLoader::test_djx_can_load_hit",
            "value": 93298.2519098519,
            "unit": "iter/sec",
            "range": "stddev: 0.0000016488812198033546",
            "extra": "mean: 10.718314432795973 usec\nrounds: 99821"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchDjxLoader::test_djx_can_load_miss",
            "value": 96307.95264782822,
            "unit": "iter/sec",
            "range": "stddev: 0.0000016978061673694058",
            "extra": "mean: 10.38335851304747 usec\nrounds: 100716"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchDjxLoader::test_djx_load_template",
            "value": 41666.00746957525,
            "unit": "iter/sec",
            "range": "stddev: 0.000003580385844224374",
            "extra": "mean: 24.000379703531845 usec\nrounds: 43663"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLayoutLoader::test_ancestor_walk_no_layouts",
            "value": 4821.513113561342,
            "unit": "iter/sec",
            "range": "stddev: 0.000009967130165437681",
            "extra": "mean: 207.4037706518575 usec\nrounds: 4927"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLayoutLoader::test_ancestor_walk_with_layouts",
            "value": 4704.085741764445,
            "unit": "iter/sec",
            "range": "stddev: 0.000010302368125190794",
            "extra": "mean: 212.58115920840174 usec\nrounds: 4849"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLoaderChain::test_build_registered_loaders_warm",
            "value": 12735384.015044168,
            "unit": "iter/sec",
            "range": "stddev: 8.215083699499549e-9",
            "extra": "mean: 78.52138567778647 nsec\nrounds: 131683"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLoaderChain::test_chain_first_hit_wins",
            "value": 27707.320130512027,
            "unit": "iter/sec",
            "range": "stddev: 0.000004670673136763441",
            "extra": "mean: 36.09154531328253 usec\nrounds: 28965"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLoaderChain::test_chain_miss_then_hit",
            "value": 94632.75583900991,
            "unit": "iter/sec",
            "range": "stddev: 0.0000015472262905775643",
            "extra": "mean: 10.56716557743715 usec\nrounds: 98824"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchComposeLayoutHierarchy::test_compose[3]",
            "value": 24177.62046057579,
            "unit": "iter/sec",
            "range": "stddev: 0.0000036906354628131656",
            "extra": "mean: 41.36056323783424 usec\nrounds: 24732"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchComposeLayoutHierarchy::test_compose[10]",
            "value": 7206.487241495816,
            "unit": "iter/sec",
            "range": "stddev: 0.000007622526822010927",
            "extra": "mean: 138.76386184962354 usec\nrounds: 7376"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_manager.py::TestBenchPageRender::test_render_simple",
            "value": 3378.260562398044,
            "unit": "iter/sec",
            "range": "stddev: 0.000042430808021331714",
            "extra": "mean: 296.0103229249298 usec\nrounds: 3651"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_manager.py::TestBenchPageRender::test_render_heavy_context",
            "value": 1098.4208365294223,
            "unit": "iter/sec",
            "range": "stddev: 0.00002795589369301736",
            "extra": "mean: 910.3978791585987 usec\nrounds: 1142"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_manager.py::TestBenchPageRender::test_build_render_context",
            "value": 8279.535819474064,
            "unit": "iter/sec",
            "range": "stddev: 0.000008426301323028343",
            "extra": "mean: 120.77971782523461 usec\nrounds: 8718"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_registry.py::TestBenchPageContextRegistry::test_register_context",
            "value": 66308.53036629435,
            "unit": "iter/sec",
            "range": "stddev: 0.0000017223941020919956",
            "extra": "mean: 15.081015888542682 usec\nrounds: 68414"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_registry.py::TestBenchPageContextRegistry::test_collect_context_single",
            "value": 11489.316464438361,
            "unit": "iter/sec",
            "range": "stddev: 0.000006826027401554204",
            "extra": "mean: 87.03737973405049 usec\nrounds: 11882"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_registry.py::TestBenchPageContextRegistry::test_collect_context_keyed_many",
            "value": 3788.6277118901216,
            "unit": "iter/sec",
            "range": "stddev: 0.000014913281267312905",
            "extra": "mean: 263.9478133102465 usec\nrounds: 3937"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_render_context.py::TestBenchBuildRenderContext::test_build_context[small]",
            "value": 4512.851611116722,
            "unit": "iter/sec",
            "range": "stddev: 0.0000276260916871333",
            "extra": "mean: 221.58938209637836 usec\nrounds: 4826"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_render_context.py::TestBenchBuildRenderContext::test_build_context[large]",
            "value": 2317.16563438387,
            "unit": "iter/sec",
            "range": "stddev: 0.000015875328120959676",
            "extra": "mean: 431.5617257399461 usec\nrounds: 2432"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_render_context.py::TestBenchPageRenderedSignal::test_render_no_receiver",
            "value": 3467.176322219041,
            "unit": "iter/sec",
            "range": "stddev: 0.000015016509572492565",
            "extra": "mean: 288.41913622667624 usec\nrounds: 3663"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_render_context.py::TestBenchPageRenderedSignal::test_render_with_receiver",
            "value": 3362.26808260697,
            "unit": "iter/sec",
            "range": "stddev: 0.000024717749606553917",
            "extra": "mean: 297.41828296589586 usec\nrounds: 3534"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_render_context.py::TestBenchPageRenderedSignal::test_render_with_receiver_large_context",
            "value": 1583.98871250165,
            "unit": "iter/sec",
            "range": "stddev: 0.000019773513986902842",
            "extra": "mean: 631.3176300484264 usec\nrounds: 1684"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchTreeSignature::test_signature[small]",
            "value": 2259.225775792544,
            "unit": "iter/sec",
            "range": "stddev: 0.000010183033309430662",
            "extra": "mean: 442.6295108328412 usec\nrounds: 2308"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchTreeSignature::test_signature[large]",
            "value": 113.8365616531227,
            "unit": "iter/sec",
            "range": "stddev: 0.00003086473938613471",
            "extra": "mean: 8.784523930432401 msec\nrounds: 115"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchCollectRoutes::test_collect_routes_cached",
            "value": 1062.7707868761902,
            "unit": "iter/sec",
            "range": "stddev: 0.0000136592785331072",
            "extra": "mean: 940.936665129183 usec\nrounds: 1084"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchCollectRoutes::test_collect_routes_fresh",
            "value": 146.66068305518425,
            "unit": "iter/sec",
            "range": "stddev: 0.00007573428894599051",
            "extra": "mean: 6.818459993287555 msec\nrounds: 149"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_unique_urls",
            "value": 29910.34019330134,
            "unit": "iter/sec",
            "range": "stddev: 0.0000021407014961796388",
            "extra": "mean: 33.43325396960741 usec\nrounds: 31299"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_dedup_hit",
            "value": 51590.733718361436,
            "unit": "iter/sec",
            "range": "stddev: 0.0000014783429015772845",
            "extra": "mean: 19.383325801472257 usec\nrounds: 53462"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_inline_unique",
            "value": 27385.759485703864,
            "unit": "iter/sec",
            "range": "stddev: 0.000002298292324259762",
            "extra": "mean: 36.51532835969103 usec\nrounds: 28149"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_js_context_many",
            "value": 3731.495492646371,
            "unit": "iter/sec",
            "range": "stddev: 0.000007936200189965723",
            "extra": "mean: 267.9890681820981 usec\nrounds: 3828"
          },
          {
            "name": "tests/benchmarks/static/test_bench_discovery.py::TestBenchPathResolver::test_find_page_root_hit_cached",
            "value": 5646483.617338301,
            "unit": "iter/sec",
            "range": "stddev: 1.3735951732734084e-8",
            "extra": "mean: 177.10137277815932 nsec\nrounds: 57664"
          },
          {
            "name": "tests/benchmarks/static/test_bench_discovery.py::TestBenchPathResolver::test_logical_name_for_template_deep",
            "value": 1395748.927468015,
            "unit": "iter/sec",
            "range": "stddev: 8.528545547766822e-8",
            "extra": "mean: 716.4612347681104 nsec\nrounds: 145922"
          },
          {
            "name": "tests/benchmarks/static/test_bench_discovery.py::TestBenchPathResolver::test_logical_name_for_layout_deep",
            "value": 1288709.8548821271,
            "unit": "iter/sec",
            "range": "stddev: 9.972061538529634e-8",
            "extra": "mean: 775.9698555975315 nsec\nrounds: 133441"
          },
          {
            "name": "tests/benchmarks/static/test_bench_serializers.py::TestBenchResolveSerializer::test_resolve_default",
            "value": 5002020.291509492,
            "unit": "iter/sec",
            "range": "stddev: 1.3998822358620437e-8",
            "extra": "mean: 199.91922097905433 nsec\nrounds: 52643"
          },
          {
            "name": "tests/benchmarks/static/test_bench_serializers.py::TestBenchJsonJsContextSerializer::test_dumps_small_dict",
            "value": 414657.4842371735,
            "unit": "iter/sec",
            "range": "stddev: 2.1100617930142458e-7",
            "extra": "mean: 2.4116289661083883 usec\nrounds: 42384"
          },
          {
            "name": "tests/benchmarks/static/test_bench_serializers.py::TestBenchJsonJsContextSerializer::test_dumps_wide_dict",
            "value": 204953.57259069761,
            "unit": "iter/sec",
            "range": "stddev: 5.879385613728215e-7",
            "extra": "mean: 4.879153787658287 usec\nrounds: 103864"
          },
          {
            "name": "tests/benchmarks/static/test_bench_serializers.py::TestBenchJsonJsContextSerializer::test_dumps_nested_dict",
            "value": 178361.077003703,
            "unit": "iter/sec",
            "range": "stddev: 9.942752429759786e-7",
            "extra": "mean: 5.606604404946708 usec\nrounds: 181160"
          },
          {
            "name": "tests/benchmarks/static/test_bench_serializers.py::TestBenchPydanticJsContextSerializer::test_dumps_model",
            "value": 214909.61605965183,
            "unit": "iter/sec",
            "range": "stddev: 6.033555947488673e-7",
            "extra": "mean: 4.653118917314677 usec\nrounds: 110169"
          },
          {
            "name": "tests/benchmarks/templatetags/test_bench_template_tags.py::TestBenchStaticTags::test_use_script_dedup",
            "value": 55261.82113818435,
            "unit": "iter/sec",
            "range": "stddev: 0.000001956024062803772",
            "extra": "mean: 18.095675810239058 usec\nrounds: 55267"
          },
          {
            "name": "tests/benchmarks/templatetags/test_bench_template_tags.py::TestBenchStaticTags::test_inline_script_block",
            "value": 111688.97558279922,
            "unit": "iter/sec",
            "range": "stddev: 0.000001333769223250843",
            "extra": "mean: 8.953435151338303 usec\nrounds: 116741"
          },
          {
            "name": "tests/benchmarks/templatetags/test_bench_template_tags.py::TestBenchStaticTags::test_collect_placeholders",
            "value": 105303.04747344833,
            "unit": "iter/sec",
            "range": "stddev: 0.0000012210320792161313",
            "extra": "mean: 9.496401329241163 usec\nrounds: 110291"
          },
          {
            "name": "tests/benchmarks/testing/test_bench_isolation.py::TestBenchResetFormActions::test_reset_form_actions_only",
            "value": 330439.2362094701,
            "unit": "iter/sec",
            "range": "stddev: 4.221081563718153e-7",
            "extra": "mean: 3.026275001332124 usec\nrounds: 200"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_backends.py::TestBenchFileRouter::test_filerouter_generate_small",
            "value": 477.9174201133086,
            "unit": "iter/sec",
            "range": "stddev: 0.00003529771694534624",
            "extra": "mean: 2.0924116969055278 msec\nrounds: 485"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_backends.py::TestBenchFileRouter::test_filerouter_generate_medium",
            "value": 116.66719628108262,
            "unit": "iter/sec",
            "range": "stddev: 0.00019741536417004402",
            "extra": "mean: 8.571389661158321 msec\nrounds: 121"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_backends.py::TestBenchFileRouter::test_filerouter_generate_large",
            "value": 10.220781716810148,
            "unit": "iter/sec",
            "range": "stddev: 0.00044555594783953915",
            "extra": "mean: 97.83987445454366 msec\nrounds: 11"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_parse_simple_segment",
            "value": 1839612.2580150773,
            "unit": "iter/sec",
            "range": "stddev: 7.355639471617835e-8",
            "extra": "mean: 543.5928118238295 nsec\nrounds: 187266"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_parse_typed_converter",
            "value": 291412.3579587858,
            "unit": "iter/sec",
            "range": "stddev: 4.2965524347244406e-7",
            "extra": "mean: 3.431563462183128 usec\nrounds: 149656"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_prepare_url_name",
            "value": 736781.6723967307,
            "unit": "iter/sec",
            "range": "stddev: 1.2443303351674166e-7",
            "extra": "mean: 1.3572541737459718 usec\nrounds: 75160"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_regex_compile_many",
            "value": 27130.939929739154,
            "unit": "iter/sec",
            "range": "stddev: 0.0000023316166904509167",
            "extra": "mean: 36.85828808694776 usec\nrounds: 27936"
          }
        ]
      },
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
          "id": "d09c726eaf5017f40c0d5f80646af361e75305a7",
          "message": "feat(core): request= hook on StaticBackend & multi-tenant notes example (#108)\n\n* feat(example): added tenant example\n\n* fix: some fixes for example",
          "timestamp": "2026-04-30T22:06:08+03:00",
          "tree_id": "a1d6bf2f55fe0e2c174a86b5594acadc5705b3ce",
          "url": "https://github.com/next-dj/next-dj/commit/d09c726eaf5017f40c0d5f80646af361e75305a7"
        },
        "date": 1777576356839,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/benchmarks/apps/test_bench_autoreload.py::TestBenchAppsAutoreload::test_install_uninstall_cycle",
            "value": 155827.84423063567,
            "unit": "iter/sec",
            "range": "stddev: 0.000006861566924975725",
            "extra": "mean: 6.417338345000351 usec\nrounds: 168606"
          },
          {
            "name": "tests/benchmarks/apps/test_bench_autoreload.py::TestBenchAppsAutoreload::test_install_idempotent",
            "value": 9734981.067857053,
            "unit": "iter/sec",
            "range": "stddev: 1.3450565916275503e-8",
            "extra": "mean: 102.7223363897233 nsec\nrounds: 93546"
          },
          {
            "name": "tests/benchmarks/components/test_bench_backends.py::TestBenchComponentScanner::test_scan_small",
            "value": 3137.675205753996,
            "unit": "iter/sec",
            "range": "stddev: 0.000012243980746421102",
            "extra": "mean: 318.7073021981878 usec\nrounds: 3276"
          },
          {
            "name": "tests/benchmarks/components/test_bench_backends.py::TestBenchComponentScanner::test_scan_large",
            "value": 66.9799054176771,
            "unit": "iter/sec",
            "range": "stddev: 0.00013254049235463636",
            "extra": "mean: 14.929850882352598 msec\nrounds: 68"
          },
          {
            "name": "tests/benchmarks/components/test_bench_facade.py::TestBenchComponentRenderedSignal::test_send_no_receiver",
            "value": 2768216.166585423,
            "unit": "iter/sec",
            "range": "stddev: 4.812027435244299e-8",
            "extra": "mean: 361.24346504106063 nsec\nrounds: 191205"
          },
          {
            "name": "tests/benchmarks/components/test_bench_facade.py::TestBenchComponentRenderedSignal::test_send_with_one_receiver",
            "value": 574196.25752109,
            "unit": "iter/sec",
            "range": "stddev: 1.548177405189462e-7",
            "extra": "mean: 1.741564816735627 usec\nrounds: 58579"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentRegistry::test_register_bulk",
            "value": 10283.85371854369,
            "unit": "iter/sec",
            "range": "stddev: 0.000008906362627873089",
            "extra": "mean: 97.23981178347714 usec\nrounds: 10642"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentRegistry::test_lookup_by_name_hit",
            "value": 8644970.399173733,
            "unit": "iter/sec",
            "range": "stddev: 1.0507578560073307e-8",
            "extra": "mean: 115.67419595740637 nsec\nrounds: 88254"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentRegistry::test_lookup_miss",
            "value": 8569972.101568416,
            "unit": "iter/sec",
            "range": "stddev: 1.077319917368886e-8",
            "extra": "mean: 116.68649420888862 nsec\nrounds: 87635"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentVisibility::test_visibility_resolve_cold",
            "value": 2337.797639608925,
            "unit": "iter/sec",
            "range": "stddev: 0.000019701094272799992",
            "extra": "mean: 427.7530197897212 usec\nrounds: 2476"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentVisibility::test_visibility_resolve_cached",
            "value": 1771903.5263381035,
            "unit": "iter/sec",
            "range": "stddev: 7.677703430140757e-8",
            "extra": "mean: 564.3648117043062 nsec\nrounds: 181819"
          },
          {
            "name": "tests/benchmarks/components/test_bench_registry.py::TestBenchComponentVisibility::test_version_bump_invalidation",
            "value": 412.57855473764766,
            "unit": "iter/sec",
            "range": "stddev: 0.00046323336414256095",
            "extra": "mean: 2.4237808497726805 msec\nrounds: 2636"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_helpers.py::TestBenchExtendDefaultBackend::test_extend_single_override",
            "value": 160215.73457959198,
            "unit": "iter/sec",
            "range": "stddev: 9.447632863894438e-7",
            "extra": "mean: 6.241584215333232 usec\nrounds: 166086"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_helpers.py::TestBenchExtendDefaultBackend::test_extend_nested_options_merge",
            "value": 149043.11971853135,
            "unit": "iter/sec",
            "range": "stddev: 0.000001001632283335408",
            "extra": "mean: 6.7094677157087474 usec\nrounds: 155958"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_merge_cold",
            "value": 36916.014044900156,
            "unit": "iter/sec",
            "range": "stddev: 0.0000024636758359436734",
            "extra": "mean: 27.088514994704507 usec\nrounds: 38480"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_merge_warm_cached",
            "value": 10007527.635643305,
            "unit": "iter/sec",
            "range": "stddev: 1.0061858592508418e-8",
            "extra": "mean: 99.92478026624184 nsec\nrounds: 99325"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_attribute_access_cached",
            "value": 5681714.133398441,
            "unit": "iter/sec",
            "range": "stddev: 1.508226239930026e-8",
            "extra": "mean: 176.0032230628723 nsec\nrounds: 58100"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_reload_cycle",
            "value": 8695.650945029525,
            "unit": "iter/sec",
            "range": "stddev: 0.000008148883755169057",
            "extra": "mean: 115.00001625198684 usec\nrounds: 9168"
          },
          {
            "name": "tests/benchmarks/conf/test_bench_settings.py::TestBenchSettingsMerge::test_merge_with_user_form_action_backends",
            "value": 38947.15672599901,
            "unit": "iter/sec",
            "range": "stddev: 0.0000020343160935878192",
            "extra": "mean: 25.67581523434942 usec\nrounds: 39910"
          },
          {
            "name": "tests/benchmarks/deps/test_bench_resolver.py::TestBenchDependencyResolver::test_resolve_simple",
            "value": 52801.35968274994,
            "unit": "iter/sec",
            "range": "stddev: 0.0000024824572357959157",
            "extra": "mean: 18.93890623287675 usec\nrounds: 52897"
          },
          {
            "name": "tests/benchmarks/deps/test_bench_resolver.py::TestBenchDependencyResolver::test_resolve_five_params",
            "value": 32232.363408590125,
            "unit": "iter/sec",
            "range": "stddev: 0.000002992003913641744",
            "extra": "mean: 31.024718458389366 usec\nrounds: 33416"
          },
          {
            "name": "tests/benchmarks/deps/test_bench_resolver.py::TestBenchDependencyResolver::test_resolve_mixed_markers",
            "value": 35848.837213752646,
            "unit": "iter/sec",
            "range": "stddev: 0.000003131576439602726",
            "extra": "mean: 27.89490755411088 usec\nrounds: 36205"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_register_bulk",
            "value": 4883.5647592924315,
            "unit": "iter/sec",
            "range": "stddev: 0.00002007235576960069",
            "extra": "mean: 204.7684528186512 usec\nrounds: 4949"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_register_bulk_with_receiver",
            "value": 2630.171278229454,
            "unit": "iter/sec",
            "range": "stddev: 0.000015175980282055824",
            "extra": "mean: 380.20337621250565 usec\nrounds: 2682"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_get_meta_hit",
            "value": 6581964.202283843,
            "unit": "iter/sec",
            "range": "stddev: 1.2676862186397843e-8",
            "extra": "mean: 151.9303310177553 nsec\nrounds: 66854"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_get_meta_miss",
            "value": 7238457.439647957,
            "unit": "iter/sec",
            "range": "stddev: 1.3068108830689716e-8",
            "extra": "mean: 138.15098152302392 nsec\nrounds: 71654"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_backends.py::TestBenchFormActionBackend::test_generate_urls_with_actions",
            "value": 210055.33864341676,
            "unit": "iter/sec",
            "range": "stddev: 8.626941590396285e-7",
            "extra": "mean: 4.760650247969028 usec\nrounds: 110291"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_checks.py::TestBenchFormActionBackendsCheck::test_check_clean",
            "value": 320315.9357170304,
            "unit": "iter/sec",
            "range": "stddev: 4.6191959349313734e-7",
            "extra": "mean: 3.121917733382481 usec\nrounds: 164447"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_checks.py::TestBenchFormActionBackendsCheck::test_check_e044_unimportable",
            "value": 10130.391876644566,
            "unit": "iter/sec",
            "range": "stddev: 0.000007193984963702742",
            "extra": "mean: 98.71286443572649 usec\nrounds: 10696"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_checks.py::TestBenchFormActionBackendsCheck::test_check_e045_wrong_subclass",
            "value": 323800.5917368455,
            "unit": "iter/sec",
            "range": "stddev: 5.803228609904504e-7",
            "extra": "mean: 3.0883204834063593 usec\nrounds: 164177"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_none",
            "value": 13347299.746827876,
            "unit": "iter/sec",
            "range": "stddev: 8.626864063979487e-9",
            "extra": "mean: 74.9215211292202 nsec\nrounds: 139587"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_httpresponse",
            "value": 7899146.976883642,
            "unit": "iter/sec",
            "range": "stddev: 1.1179076691787099e-8",
            "extra": "mean: 126.59594800887199 nsec\nrounds: 81281"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_str",
            "value": 6527143.774207865,
            "unit": "iter/sec",
            "range": "stddev: 2.080362066759648e-8",
            "extra": "mean: 153.20636936963442 nsec\nrounds: 69459"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_normalize_redirect_duck",
            "value": 156463.4070303603,
            "unit": "iter/sec",
            "range": "stddev: 0.0000011605593448316261",
            "extra": "mean: 6.391270770461741 usec\nrounds: 162285"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_filter_reserved_url_kwargs",
            "value": 543478.4856217274,
            "unit": "iter/sec",
            "range": "stddev: 1.5244263854215656e-7",
            "extra": "mean: 1.8399992390793944 usec\nrounds: 54014"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchHelpers::test_url_kwargs_from_post",
            "value": 124274.06445585475,
            "unit": "iter/sec",
            "range": "stddev: 0.0000010971398807121483",
            "extra": "mean: 8.046731265920936 usec\nrounds: 128123"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchEndToEnd::test_dispatch_valid_form",
            "value": 4001.7677483417206,
            "unit": "iter/sec",
            "range": "stddev: 0.000019058973906441",
            "extra": "mean: 249.88956453417038 usec\nrounds: 4331"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchEndToEnd::test_dispatch_invalid_form",
            "value": 4045.2616751380524,
            "unit": "iter/sec",
            "range": "stddev: 0.00001638087755882242",
            "extra": "mean: 247.20279682917499 usec\nrounds: 4730"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_dispatch.py::TestBenchDispatchEndToEnd::test_dispatch_through_subclassed_backend",
            "value": 4174.228806935201,
            "unit": "iter/sec",
            "range": "stddev: 0.000017802945221030607",
            "extra": "mean: 239.56520982715827 usec\nrounds: 4742"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_factory.py::TestBenchFormActionFactory::test_create_backend_cached",
            "value": 2580711.3906823266,
            "unit": "iter/sec",
            "range": "stddev: 5.08349064751896e-8",
            "extra": "mean: 387.4900554980715 nsec\nrounds: 186916"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_factory.py::TestBenchFormActionFactory::test_create_backend_cold",
            "value": 879078.3736067372,
            "unit": "iter/sec",
            "range": "stddev: 5.016033609826493e-7",
            "extra": "mean: 1.137555000809698 usec\nrounds: 200"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchEnsureBackends::test_ensure_backends_warm",
            "value": 10055395.955030644,
            "unit": "iter/sec",
            "range": "stddev: 1.046462119578108e-8",
            "extra": "mean: 99.44909225575618 nsec\nrounds: 103972"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchEnsureBackends::test_reload_config_cold",
            "value": 1182332.8601438734,
            "unit": "iter/sec",
            "range": "stddev: 1.4694223462246937e-7",
            "extra": "mean: 845.7855090641007 nsec\nrounds: 118695"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchRegisterThroughManager::test_register_bulk_via_manager",
            "value": 4511.867798543402,
            "unit": "iter/sec",
            "range": "stddev: 0.000008803878090220043",
            "extra": "mean: 221.63769965131445 usec\nrounds: 4588"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchManagerLookups::test_meta_lookup_through_manager",
            "value": 5344546.233036871,
            "unit": "iter/sec",
            "range": "stddev: 1.5179728017373606e-8",
            "extra": "mean: 187.1066235368276 nsec\nrounds: 54101"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchManagerLookups::test_get_action_url_miss",
            "value": 1497382.0492820549,
            "unit": "iter/sec",
            "range": "stddev: 1.170635901405875e-7",
            "extra": "mean: 667.8322345853331 nsec\nrounds: 158454"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchManagerLookups::test_default_backend_property",
            "value": 6684379.408277368,
            "unit": "iter/sec",
            "range": "stddev: 1.5193255871448908e-8",
            "extra": "mean: 149.60251938447493 nsec\nrounds: 68743"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchManagerLookups::test_iter_url_patterns",
            "value": 184853.3458485155,
            "unit": "iter/sec",
            "range": "stddev: 0.000001638947077619054",
            "extra": "mean: 5.409693805701979 usec\nrounds: 190477"
          },
          {
            "name": "tests/benchmarks/forms/test_bench_manager.py::TestBenchClearRegistries::test_clear_registries",
            "value": 243695.00112292866,
            "unit": "iter/sec",
            "range": "stddev: 7.364075419934852e-7",
            "extra": "mean: 4.10348999935195 usec\nrounds: 200"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchPythonModuleLoader::test_python_load_cold",
            "value": 10975.59904703193,
            "unit": "iter/sec",
            "range": "stddev: 0.000015831126355499145",
            "extra": "mean: 91.11120000966366 usec\nrounds: 20"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchPythonModuleLoader::test_python_load_warm_mtime_hit",
            "value": 327528.8432911867,
            "unit": "iter/sec",
            "range": "stddev: 5.322156855247288e-7",
            "extra": "mean: 3.053166218741104 usec\nrounds: 168919"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchPythonModuleLoader::test_python_template_loader_can_load",
            "value": 308100.06561383914,
            "unit": "iter/sec",
            "range": "stddev: 5.064430976003239e-7",
            "extra": "mean: 3.2456987570179936 usec\nrounds: 158958"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchDjxLoader::test_djx_can_load_hit",
            "value": 93311.86275978449,
            "unit": "iter/sec",
            "range": "stddev: 0.0000018152476493309332",
            "extra": "mean: 10.71675101561663 usec\nrounds: 96994"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchDjxLoader::test_djx_can_load_miss",
            "value": 95020.96454175087,
            "unit": "iter/sec",
            "range": "stddev: 0.0000020362833218995954",
            "extra": "mean: 10.523993361071536 usec\nrounds: 99711"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchDjxLoader::test_djx_load_template",
            "value": 41511.25280221434,
            "unit": "iter/sec",
            "range": "stddev: 0.000004612949517299283",
            "extra": "mean: 24.089853533561794 usec\nrounds: 43341"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLayoutLoader::test_ancestor_walk_no_layouts",
            "value": 4839.049079439692,
            "unit": "iter/sec",
            "range": "stddev: 0.000010316948346126128",
            "extra": "mean: 206.6521714460042 usec\nrounds: 4987"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLayoutLoader::test_ancestor_walk_with_layouts",
            "value": 4722.291547988281,
            "unit": "iter/sec",
            "range": "stddev: 0.000014989453479072383",
            "extra": "mean: 211.76159706318953 usec\nrounds: 4904"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLoaderChain::test_build_registered_loaders_warm",
            "value": 12816169.748415543,
            "unit": "iter/sec",
            "range": "stddev: 9.707061888491405e-9",
            "extra": "mean: 78.02643220480358 nsec\nrounds: 125408"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLoaderChain::test_chain_first_hit_wins",
            "value": 27230.742038901557,
            "unit": "iter/sec",
            "range": "stddev: 0.0000053505641797292215",
            "extra": "mean: 36.723200512546086 usec\nrounds: 28881"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchLoaderChain::test_chain_miss_then_hit",
            "value": 93224.54929683694,
            "unit": "iter/sec",
            "range": "stddev: 0.0000018919442464017029",
            "extra": "mean: 10.726788249905 usec\nrounds: 98824"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchComposeLayoutHierarchy::test_compose[3]",
            "value": 23803.495688995146,
            "unit": "iter/sec",
            "range": "stddev: 0.000004529850438470085",
            "extra": "mean: 42.01063629752167 usec\nrounds: 24652"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_loaders.py::TestBenchComposeLayoutHierarchy::test_compose[10]",
            "value": 7108.549206916972,
            "unit": "iter/sec",
            "range": "stddev: 0.000008096470815242165",
            "extra": "mean: 140.6756809148835 usec\nrounds: 7299"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_manager.py::TestBenchPageRender::test_render_simple",
            "value": 3352.003280824276,
            "unit": "iter/sec",
            "range": "stddev: 0.000015859385460841944",
            "extra": "mean: 298.3290636141903 usec\nrounds: 3537"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_manager.py::TestBenchPageRender::test_render_heavy_context",
            "value": 1068.133863039705,
            "unit": "iter/sec",
            "range": "stddev: 0.00001765394783538243",
            "extra": "mean: 936.2122432428002 usec\nrounds: 1110"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_manager.py::TestBenchPageRender::test_build_render_context",
            "value": 8054.318851411128,
            "unit": "iter/sec",
            "range": "stddev: 0.000017162358369311212",
            "extra": "mean: 124.15699185099912 usec\nrounds: 8468"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_registry.py::TestBenchPageContextRegistry::test_register_context",
            "value": 66938.37288502345,
            "unit": "iter/sec",
            "range": "stddev: 0.000001529552312550473",
            "extra": "mean: 14.939114246437509 usec\nrounds: 67582"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_registry.py::TestBenchPageContextRegistry::test_collect_context_single",
            "value": 11125.418126962059,
            "unit": "iter/sec",
            "range": "stddev: 0.000011295067380367378",
            "extra": "mean: 89.88426219923683 usec\nrounds: 11804"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_registry.py::TestBenchPageContextRegistry::test_collect_context_keyed_many",
            "value": 3726.589990214683,
            "unit": "iter/sec",
            "range": "stddev: 0.000013182309448297912",
            "extra": "mean: 268.341836001763 usec\nrounds: 3872"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_render_context.py::TestBenchBuildRenderContext::test_build_context[small]",
            "value": 4455.376979696309,
            "unit": "iter/sec",
            "range": "stddev: 0.000015407655585395585",
            "extra": "mean: 224.44789847348963 usec\nrounds: 4718"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_render_context.py::TestBenchBuildRenderContext::test_build_context[large]",
            "value": 2267.051897308974,
            "unit": "iter/sec",
            "range": "stddev: 0.00001872671943916803",
            "extra": "mean: 441.1015033167153 usec\nrounds: 2412"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_render_context.py::TestBenchPageRenderedSignal::test_render_no_receiver",
            "value": 3357.778748507924,
            "unit": "iter/sec",
            "range": "stddev: 0.00002087447085282526",
            "extra": "mean: 297.8159297852379 usec\nrounds: 3589"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_render_context.py::TestBenchPageRenderedSignal::test_render_with_receiver",
            "value": 3287.778463571193,
            "unit": "iter/sec",
            "range": "stddev: 0.000016742727567494526",
            "extra": "mean: 304.15674628934624 usec\nrounds: 3504"
          },
          {
            "name": "tests/benchmarks/pages/test_bench_render_context.py::TestBenchPageRenderedSignal::test_render_with_receiver_large_context",
            "value": 1535.516678680107,
            "unit": "iter/sec",
            "range": "stddev: 0.00002452089596617271",
            "extra": "mean: 651.2465894278504 usec\nrounds: 1627"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchTreeSignature::test_signature[small]",
            "value": 2215.9333883090712,
            "unit": "iter/sec",
            "range": "stddev: 0.000016887994317322596",
            "extra": "mean: 451.27710303741463 usec\nrounds: 2271"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchTreeSignature::test_signature[large]",
            "value": 111.48638150632502,
            "unit": "iter/sec",
            "range": "stddev: 0.00006786408803413323",
            "extra": "mean: 8.969705415932497 msec\nrounds: 113"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchCollectRoutes::test_collect_routes_cached",
            "value": 1033.8787315621246,
            "unit": "iter/sec",
            "range": "stddev: 0.000032136688322357627",
            "extra": "mean: 967.2314261548489 usec\nrounds: 1063"
          },
          {
            "name": "tests/benchmarks/server/test_bench_autoreload.py::TestBenchCollectRoutes::test_collect_routes_fresh",
            "value": 144.08967621929648,
            "unit": "iter/sec",
            "range": "stddev: 0.000052146512897633875",
            "extra": "mean: 6.940122472605571 msec\nrounds: 146"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_unique_urls",
            "value": 30321.870478556084,
            "unit": "iter/sec",
            "range": "stddev: 0.000002348593254429818",
            "extra": "mean: 32.97949579684438 usec\nrounds: 31759"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_dedup_hit",
            "value": 52363.307963398525,
            "unit": "iter/sec",
            "range": "stddev: 0.000001832451315895909",
            "extra": "mean: 19.097341991819743 usec\nrounds: 53320"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_inline_unique",
            "value": 27917.452087420013,
            "unit": "iter/sec",
            "range": "stddev: 0.0000025877887325073094",
            "extra": "mean: 35.81988774866076 usec\nrounds: 28650"
          },
          {
            "name": "tests/benchmarks/static/test_bench_collector.py::TestBenchStaticCollector::test_add_js_context_many",
            "value": 3611.818291172447,
            "unit": "iter/sec",
            "range": "stddev: 0.000018450101402574276",
            "extra": "mean: 276.86885645495363 usec\nrounds: 3741"
          },
          {
            "name": "tests/benchmarks/static/test_bench_discovery.py::TestBenchPathResolver::test_find_page_root_hit_cached",
            "value": 5805605.38662455,
            "unit": "iter/sec",
            "range": "stddev: 1.4333142447416467e-8",
            "extra": "mean: 172.2473253700442 nsec\nrounds: 58610"
          },
          {
            "name": "tests/benchmarks/static/test_bench_discovery.py::TestBenchPathResolver::test_logical_name_for_template_deep",
            "value": 1407377.8380808728,
            "unit": "iter/sec",
            "range": "stddev: 9.138947499515018e-8",
            "extra": "mean: 710.5412441079923 nsec\nrounds: 145519"
          },
          {
            "name": "tests/benchmarks/static/test_bench_discovery.py::TestBenchPathResolver::test_logical_name_for_layout_deep",
            "value": 1266348.3930532995,
            "unit": "iter/sec",
            "range": "stddev: 1.1677226368845448e-7",
            "extra": "mean: 789.6721040478399 nsec\nrounds: 131338"
          },
          {
            "name": "tests/benchmarks/static/test_bench_serializers.py::TestBenchResolveSerializer::test_resolve_default",
            "value": 4945908.7021203665,
            "unit": "iter/sec",
            "range": "stddev: 2.0473313394409958e-8",
            "extra": "mean: 202.18731485506166 nsec\nrounds: 51744"
          },
          {
            "name": "tests/benchmarks/static/test_bench_serializers.py::TestBenchJsonJsContextSerializer::test_dumps_small_dict",
            "value": 415647.49434737855,
            "unit": "iter/sec",
            "range": "stddev: 2.1493840919557934e-7",
            "extra": "mean: 2.4058848269255946 usec\nrounds: 42259"
          },
          {
            "name": "tests/benchmarks/static/test_bench_serializers.py::TestBenchJsonJsContextSerializer::test_dumps_wide_dict",
            "value": 206959.81260933666,
            "unit": "iter/sec",
            "range": "stddev: 6.164653547820029e-7",
            "extra": "mean: 4.831855940494249 usec\nrounds: 106758"
          },
          {
            "name": "tests/benchmarks/static/test_bench_serializers.py::TestBenchJsonJsContextSerializer::test_dumps_nested_dict",
            "value": 176525.70435343895,
            "unit": "iter/sec",
            "range": "stddev: 9.682452874612132e-7",
            "extra": "mean: 5.6648973794649455 usec\nrounds: 179857"
          },
          {
            "name": "tests/benchmarks/static/test_bench_serializers.py::TestBenchPydanticJsContextSerializer::test_dumps_model",
            "value": 214364.0127745727,
            "unit": "iter/sec",
            "range": "stddev: 6.820427811084974e-7",
            "extra": "mean: 4.664962122404425 usec\nrounds: 109207"
          },
          {
            "name": "tests/benchmarks/templatetags/test_bench_template_tags.py::TestBenchStaticTags::test_use_script_dedup",
            "value": 55615.43463138229,
            "unit": "iter/sec",
            "range": "stddev: 0.000002175093081398746",
            "extra": "mean: 17.980620067576115 usec\nrounds: 57068"
          },
          {
            "name": "tests/benchmarks/templatetags/test_bench_template_tags.py::TestBenchStaticTags::test_inline_script_block",
            "value": 110881.56555693356,
            "unit": "iter/sec",
            "range": "stddev: 0.000003840719944246387",
            "extra": "mean: 9.018631681264791 usec\nrounds: 116334"
          },
          {
            "name": "tests/benchmarks/templatetags/test_bench_template_tags.py::TestBenchStaticTags::test_collect_placeholders",
            "value": 104900.43698046908,
            "unit": "iter/sec",
            "range": "stddev: 0.0000014684628266312477",
            "extra": "mean: 9.532848754349663 usec\nrounds: 109927"
          },
          {
            "name": "tests/benchmarks/testing/test_bench_isolation.py::TestBenchResetFormActions::test_reset_form_actions_only",
            "value": 329369.91541021515,
            "unit": "iter/sec",
            "range": "stddev: 8.345926921397738e-7",
            "extra": "mean: 3.036099999462749 usec\nrounds: 200"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_backends.py::TestBenchFileRouter::test_filerouter_generate_small",
            "value": 466.4695334401169,
            "unit": "iter/sec",
            "range": "stddev: 0.00007251719191647782",
            "extra": "mean: 2.1437627289936936 msec\nrounds: 476"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_backends.py::TestBenchFileRouter::test_filerouter_generate_medium",
            "value": 112.9857114503214,
            "unit": "iter/sec",
            "range": "stddev: 0.0004347781450758985",
            "extra": "mean: 8.85067666666585 msec\nrounds: 117"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_backends.py::TestBenchFileRouter::test_filerouter_generate_large",
            "value": 9.832822286650632,
            "unit": "iter/sec",
            "range": "stddev: 0.0011641419451420417",
            "extra": "mean: 101.7002007000201 msec\nrounds: 10"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_parse_simple_segment",
            "value": 1826802.4390023332,
            "unit": "iter/sec",
            "range": "stddev: 8.105834738603192e-8",
            "extra": "mean: 547.4045680309729 nsec\nrounds: 185529"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_parse_typed_converter",
            "value": 288231.8167604981,
            "unit": "iter/sec",
            "range": "stddev: 4.575577384023594e-7",
            "extra": "mean: 3.469429611342786 usec\nrounds: 148987"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_prepare_url_name",
            "value": 766469.2029510094,
            "unit": "iter/sec",
            "range": "stddev: 1.309863187394494e-7",
            "extra": "mean: 1.304683862247649 usec\nrounds: 75444"
          },
          {
            "name": "tests/benchmarks/urls/test_bench_parser.py::TestBenchURLParser::test_regex_compile_many",
            "value": 27235.06412971539,
            "unit": "iter/sec",
            "range": "stddev: 0.000002625695551536301",
            "extra": "mean: 36.71737269415602 usec\nrounds: 27811"
          }
        ]
      }
    ]
  }
}