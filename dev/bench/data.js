window.BENCHMARK_DATA = {
  "lastUpdate": 1776979154966,
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
      }
    ]
  }
}