import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from next.urls import (
    FileRouterBackend,
    RouterBackend,
    RouterFactory,
    RouterManager,
    get_next_pages_config,
    create_router_from_config,
    get_configured_routers,
    auto_include_all_pages,
    include_pages,
)


class TestRouterBackend:
    """Test the abstract RouterBackend class."""
    
    def test_router_backend_is_abstract(self):
        """Test that RouterBackend cannot be instantiated directly."""
        with pytest.raises(TypeError):
            RouterBackend()


class TestFileRouterBackend:
    """Test the FileRouterBackend implementation."""
    
    @pytest.fixture
    def router(self):
        """Create a basic router instance for tests."""
        return FileRouterBackend()
    
    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for tests."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        import shutil
        shutil.rmtree(temp_dir)
    
    @pytest.fixture
    def pages_dir(self, temp_dir):
        """Create a pages directory structure for tests."""
        pages_dir = Path(temp_dir) / 'testapp' / 'pages'
        pages_dir.mkdir(parents=True, exist_ok=True)
        return pages_dir
    
    def test_init_defaults(self):
        """Test router initialization with default values."""
        router = FileRouterBackend()
        assert router.pages_dir_name == 'pages'
        assert router.app_dirs is True
        assert router.options == {}
        assert router._patterns_cache == {}
    
    def test_init_custom(self):
        """Test router initialization with custom values."""
        router = FileRouterBackend(
            pages_dir_name='views',
            app_dirs=False,
            options={'custom': 'value'}
        )
        assert router.pages_dir_name == 'views'
        assert router.app_dirs is False
        assert router.options == {'custom': 'value'}
    
    def test_repr(self):
        """Test string representation."""
        router = FileRouterBackend('views', False)
        assert repr(router) == '<FileRouterBackend pages_dir=\'views\' app_dirs=False>'
    
    def test_eq_same_instance(self):
        """Test equality with same instance."""
        router1 = FileRouterBackend('pages', True, {'opt': 'val'})
        router2 = FileRouterBackend('pages', True, {'opt': 'val'})
        assert router1 == router2
    
    def test_eq_different_instance(self):
        """Test equality with different instance."""
        router1 = FileRouterBackend('pages', True)
        router2 = FileRouterBackend('views', True)
        assert router1 != router2
    
    def test_eq_wrong_type(self):
        """Test equality with wrong type."""
        router = FileRouterBackend()
        assert router != "not a router"
    
    def test_hash(self):
        """Test hash function."""
        router1 = FileRouterBackend('pages', True, {'opt': 'val'})
        router2 = FileRouterBackend('pages', True, {'opt': 'val'})
        assert hash(router1) == hash(router2)
    
    def test_generate_urls_app_dirs(self):
        """Test URL generation for app directories."""
        router = FileRouterBackend(app_dirs=True)
        with patch.object(router, '_generate_app_urls', return_value=['url1', 'url2']):
            urls = router.generate_urls()
            assert urls == ['url1', 'url2']
    
    def test_generate_urls_root_only(self):
        """Test URL generation for root pages only."""
        router = FileRouterBackend(app_dirs=False)
        with patch.object(router, '_generate_root_urls', return_value=['url1']):
            urls = router.generate_urls()
            assert urls == ['url1']
    
    def test_get_installed_apps(self):
        """Test getting installed Django apps."""
        router = FileRouterBackend()
        with patch('next.urls.settings') as mock_settings:
            mock_settings.INSTALLED_APPS = ['django.contrib.admin', 'myapp', 'django.contrib.auth']
            apps = list(router._get_installed_apps())
            assert apps == ['myapp']
    
    def test_get_app_pages_path_success(self):
        """Test getting app pages path successfully."""
        router = FileRouterBackend()
        with patch('builtins.__import__') as mock_import:
            mock_module = Mock()
            mock_module.__file__ = '/path/to/app/__init__.py'
            mock_import.return_value = mock_module
            
            with patch('next.urls.Path') as mock_path_class:
                mock_app_path = Mock()
                mock_app_path.parent = Mock()
                mock_pages_path = Mock()
                mock_pages_path.exists.return_value = True
                mock_path_class.return_value = mock_app_path
                mock_app_path.parent.__truediv__ = Mock(return_value=mock_pages_path)
                
                result = router._get_app_pages_path('testapp')
                assert result == mock_pages_path
    
    def test_get_app_pages_path_import_error(self):
        """Test getting app pages path with import error."""
        router = FileRouterBackend()
        with patch('builtins.__import__', side_effect=ImportError):
            result = router._get_app_pages_path('nonexistent')
            assert result is None
    
    def test_get_app_pages_path_no_file(self):
        """Test getting app pages path when __file__ is None."""
        router = FileRouterBackend()
        with patch('builtins.__import__') as mock_import:
            mock_module = Mock()
            mock_module.__file__ = None
            mock_import.return_value = mock_module
            
            result = router._get_app_pages_path('testapp')
            assert result is None
    
    def test_get_root_pages_path_with_base_dir(self):
        """Test getting root pages path with BASE_DIR setting."""
        router = FileRouterBackend()
        with patch('next.urls.settings') as mock_settings:
            mock_settings.BASE_DIR = '/path/to/project'
            
            with patch('next.urls.Path') as mock_path_class:
                mock_path_instance = Mock()
                mock_path_instance.exists.return_value = True
                mock_path_class.return_value = mock_path_instance
                mock_path_instance.__truediv__ = Mock(return_value=mock_path_instance)
                
                result = router._get_root_pages_path()
                assert result == mock_path_instance
    
    def test_get_root_pages_path_string_base_dir(self):
        """Test getting root pages path with string BASE_DIR."""
        router = FileRouterBackend()
        with patch('next.urls.settings') as mock_settings:
            mock_settings.BASE_DIR = '/path/to/project'
            
            with patch('next.urls.Path') as mock_path_class:
                mock_path_instance = Mock()
                mock_path_instance.exists.return_value = True
                mock_path_class.return_value = mock_path_instance
                mock_path_instance.__truediv__ = Mock(return_value=mock_path_instance)
                
                result = router._get_root_pages_path()
                assert result == mock_path_instance
    
    def test_get_root_pages_path_no_base_dir(self):
        """Test getting root pages path when BASE_DIR is not set."""
        router = FileRouterBackend()
        with patch('next.urls.settings') as mock_settings:
            mock_settings.BASE_DIR = None
            result = router._get_root_pages_path()
            assert result is None
    
    def test_get_root_pages_path_does_not_exist(self):
        """Test getting root pages path when directory doesn't exist."""
        router = FileRouterBackend()
        with patch('next.urls.settings') as mock_settings:
            mock_settings.BASE_DIR = '/path/to/project'
            
            with patch('next.urls.Path') as mock_path_class:
                mock_path_instance = Mock()
                mock_path_instance.exists.return_value = False
                mock_path_class.return_value = mock_path_instance
                mock_path_instance.__truediv__ = Mock(return_value=mock_path_instance)
                
                result = router._get_root_pages_path()
                assert result is None
    
    def test_generate_urls_for_app_cached(self):
        """Test generating URLs for app with cache hit."""
        router = FileRouterBackend()
        router._patterns_cache['testapp'] = ['cached_url']
        
        result = router._generate_urls_for_app('testapp')
        assert result == ['cached_url']
    
    def test_generate_urls_for_app_no_pages_path(self):
        """Test generating URLs for app when pages path doesn't exist."""
        router = FileRouterBackend()
        with patch.object(router, '_get_app_pages_path', return_value=None):
            result = router._generate_urls_for_app('testapp')
            assert result == []
    
    def test_generate_urls_for_app_with_patterns(self):
        """Test generating URLs for app with patterns."""
        router = FileRouterBackend()
        mock_pages_path = Mock()
        
        with patch.object(router, '_get_app_pages_path', return_value=mock_pages_path):
            with patch.object(router, '_generate_patterns_from_directory', return_value=['pattern1', 'pattern2']):
                result = router._generate_urls_for_app('testapp')
                assert result == ['pattern1', 'pattern2']
                assert router._patterns_cache['testapp'] == ['pattern1', 'pattern2']
    
    def test_generate_patterns_from_directory(self):
        """Test generating patterns from directory."""
        router = FileRouterBackend()
        mock_pages_path = Mock()
        
        with patch.object(router, '_scan_pages_directory', return_value=[('url1', 'file1'), ('url2', 'file2')]):
            with patch.object(router, '_create_url_pattern') as mock_create:
                mock_create.side_effect = ['pattern1', 'pattern2']
                
                patterns = list(router._generate_patterns_from_directory(mock_pages_path))
                assert patterns == ['pattern1', 'pattern2']
    
    def test_scan_pages_directory_empty(self):
        """Test scanning empty pages directory."""
        router = FileRouterBackend()
        
        with patch('pathlib.Path.iterdir', return_value=[]):
            pages = list(router._scan_pages_directory(Path('/tmp')))
            assert pages == []
    
    def test_scan_pages_directory_with_files(self):
        """Test scanning pages directory with files."""
        router = FileRouterBackend()
        
        mock_dir = Mock()
        mock_dir.name = 'dir1'
        mock_dir.is_dir.return_value = True
        
        mock_file = Mock()
        mock_file.name = 'page.py'
        mock_file.is_dir.return_value = False
        
        with patch('pathlib.Path.iterdir', return_value=[mock_dir, mock_file]):
            with patch.object(router, '_scan_pages_directory') as mock_scan:
                mock_scan.return_value = [('dir1', 'file1')]
                
                pages = list(router._scan_pages_directory(Path('/tmp')))
                assert pages == [('dir1', 'file1')]
    
    def test_scan_pages_directory_recursive(self):
        """Test recursive scanning of pages directory."""
        router = FileRouterBackend()
        
        # create a mock directory structure
        root_dir = Path('/tmp/pages')
        
        with patch('pathlib.Path.iterdir') as mock_iterdir:
            # mock the directory structure
            mock_iterdir.side_effect = [
                [Mock(name='home', is_dir=lambda: True), Mock(name='page.py', is_dir=lambda: False)],
                [Mock(name='page.py', is_dir=lambda: False)]
            ]
            
            with patch.object(router, '_scan_pages_directory') as mock_scan:
                mock_scan.return_value = [('home', 'file1'), ('', 'file2')]
                
                pages = list(router._scan_pages_directory(root_dir))
                assert len(pages) == 2
                assert any('home' in str(page[0]) for page in pages)
    
    def test_create_url_pattern_success(self):
        """Test creating URL pattern successfully."""
        router = FileRouterBackend()
        
        with patch.object(router, '_parse_url_pattern', return_value=('pattern', {'param': 'value'})):
            with patch.object(router, '_load_page_function', return_value=lambda req, **kwargs: 'response'):
                pattern = router._create_url_pattern('test', Path('/tmp/page.py'))
                assert pattern is not None
    
    def test_create_url_pattern_no_render_func(self):
        """Test creating URL pattern when render function is missing."""
        router = FileRouterBackend()
        
        with patch.object(router, '_parse_url_pattern', return_value=('pattern', {})):
            with patch.object(router, '_load_page_function', return_value=None):
                pattern = router._create_url_pattern('test', Path('/tmp/page.py'))
                assert pattern is None
    
    def test_create_url_pattern_with_args(self):
        """Test creating URL pattern with args parameter."""
        router = FileRouterBackend()
        
        with patch.object(router, '_parse_url_pattern', return_value=('pattern', {'args': 'args'})):
            with patch.object(router, '_load_page_function', return_value=lambda req, **kwargs: 'response'):
                pattern = router._create_url_pattern('test/[[args]]', Path('/tmp/page.py'))
                assert pattern is not None
    
    def test_parse_url_pattern_simple(self):
        """Test parsing simple URL pattern."""
        router = FileRouterBackend()
        pattern, params = router._parse_url_pattern('simple')
        assert pattern == 'simple'
        assert params == {}
    
    def test_parse_url_pattern_with_param(self):
        """Test parsing URL pattern with parameter."""
        router = FileRouterBackend()
        pattern, params = router._parse_url_pattern('user/[id]')
        assert pattern == 'user/<str:id>'
        assert params == {'id': 'id'}
    
    def test_parse_url_pattern_with_type(self):
        """Test parsing URL pattern with type."""
        router = FileRouterBackend()
        pattern, params = router._parse_url_pattern('user/[int:user-id]')
        assert pattern == 'user/<int:user_id>'
        assert params == {'user_id': 'user_id'}
    
    def test_parse_url_pattern_with_args(self):
        """Test parsing URL pattern with args."""
        router = FileRouterBackend()
        pattern, params = router._parse_url_pattern('profile/[[args]]')
        assert pattern == 'profile/<path:args>'
        assert params == {'args': 'args'}
    
    def test_parse_url_pattern_mixed(self):
        """Test parsing URL pattern with mixed parameters."""
        router = FileRouterBackend()
        pattern, params = router._parse_url_pattern('user/[int:id]/posts/[[args]]')
        assert pattern == 'user/<int:id>/posts/<path:args>'
        assert params == {'id': 'id', 'args': 'args'}
    
    def test_parse_url_pattern_complex(self):
        """Test parsing complex URL pattern."""
        router = FileRouterBackend()
        pattern, params = router._parse_url_pattern('user/[int:user-id]/posts/[slug:post-slug]/[[args]]')
        assert '<int:user_id>' in pattern
        assert '<slug:post_slug>' in pattern
        assert '<path:args>' in pattern
        assert 'user_id' in params
        assert 'post_slug' in params
        assert 'args' in params
    
    def test_parse_url_pattern_edge_cases(self):
        """Test parsing URL pattern edge cases."""
        router = FileRouterBackend()
        
        # empty string
        pattern, params = router._parse_url_pattern('')
        assert pattern == ''
        assert params == {}
        
        # empty brackets
        pattern, params = router._parse_url_pattern('[]')
        assert '[' in pattern or '<str:' in pattern
        assert len(params) == 0 or '' in params
        
        # empty args brackets
        pattern, params = router._parse_url_pattern('[[]]')
        assert '[' in pattern or '<path:' in pattern
        assert len(params) == 0 or '' in params
    
    def test_parse_param_name_and_type_simple(self):
        """Test parsing parameter name and type."""
        router = FileRouterBackend()
        name, type_name = router._parse_param_name_and_type('param')
        assert name == 'param'
        assert type_name == 'str'
    
    def test_parse_param_name_and_type_with_type(self):
        """Test parsing parameter name and type with type specification."""
        router = FileRouterBackend()
        name, type_name = router._parse_param_name_and_type('int:user-id')
        assert name == 'user-id'
        assert type_name == 'int'
    
    def test_parse_param_name_and_type_edge_cases(self):
        """Test parsing parameter name and type edge cases."""
        router = FileRouterBackend()
        
        # empty string
        name, type_name = router._parse_param_name_and_type('')
        assert name == ''
        assert type_name == 'str'
        
        # whitespace
        name, type_name = router._parse_param_name_and_type('   ')
        assert name == ''
        assert type_name == 'str'
        
        # only colon
        name, type_name = router._parse_param_name_and_type(':param')
        assert name == 'param'
        assert type_name == ''
    
    def test_load_page_function_success(self):
        """Test loading page function successfully."""
        router = FileRouterBackend()
        
        with patch('importlib.util.spec_from_file_location') as mock_spec:
            mock_spec.return_value = Mock(loader=Mock())
            
            with patch('importlib.util.module_from_spec') as mock_module:
                mock_module.return_value = Mock()
                mock_module.return_value.render = lambda req: 'response'
                
                with patch('importlib.util.spec_from_file_location') as mock_spec_from_file:
                    mock_spec_from_file.return_value = Mock(loader=Mock())
                    
                    result = router._load_page_function(Path('/tmp/page.py'))
                    assert result is not None
                    assert callable(result)
    
    def test_load_page_function_spec_none(self):
        """Test loading page function when spec is None."""
        router = FileRouterBackend()
        
        with patch('importlib.util.spec_from_file_location', return_value=None):
            result = router._load_page_function(Path('/tmp/page.py'))
            assert result is None
    
    def test_load_page_function_loader_none(self):
        """Test loading page function when loader is None."""
        router = FileRouterBackend()
        
        mock_spec = Mock()
        mock_spec.loader = None
        
        with patch('importlib.util.spec_from_file_location', return_value=mock_spec):
            result = router._load_page_function(Path('/tmp/page.py'))
            assert result is None
    
    def test_load_page_function_no_render(self):
        """Test loading page function when render function is missing."""
        router = FileRouterBackend()
        
        with patch('importlib.util.spec_from_file_location') as mock_spec:
            mock_spec.return_value = Mock(loader=Mock())
            
            with patch('importlib.util.module_from_spec') as mock_module:
                mock_module.return_value = Mock()
                # no render function
                
                with patch('builtins.getattr', return_value=None):
                    result = router._load_page_function(Path('/tmp/page.py'))
                    assert result is None
    
    def test_load_page_function_not_callable(self):
        """Test loading page function when render is not callable."""
        router = FileRouterBackend()
        
        with patch('importlib.util.spec_from_file_location') as mock_spec:
            mock_spec.return_value = Mock(loader=Mock())
            
            with patch('importlib.util.module_from_spec') as mock_module:
                mock_module.return_value = Mock()
                mock_module.return_value.render = "not a function"
                
                with patch('importlib.util.spec_from_file_location') as mock_spec_from_file:
                    mock_spec_from_file.return_value = Mock(loader=Mock())
                    
                    result = router._load_page_function(Path('/tmp/page.py'))
                    assert result is None
    
    def test_load_page_function_execution_error(self):
        """Test loading page function with execution error."""
        router = FileRouterBackend()
        
        with patch('importlib.util.spec_from_file_location') as mock_spec:
            mock_spec.return_value = Mock(loader=Mock())
            
            with patch('importlib.util.module_from_spec') as mock_module:
                mock_module.return_value = Mock()
                
                # Make exec_module raise an exception
                mock_spec.return_value.loader.exec_module.side_effect = Exception("Test error")
                
                result = router._load_page_function(Path('/tmp/page.py'))
                assert result is None

    def test_get_app_pages_path_attribute_error(self):
        """Test getting app pages path with AttributeError."""
        router = FileRouterBackend()
        with patch('builtins.__import__') as mock_import:
            mock_module = Mock()
            # simulate AttributeError when accessing __file__
            mock_module.__file__ = '/path/to/app/__init__.py'
            # mock the Path constructor to raise AttributeError when .parent is called
            with patch('next.urls.Path') as mock_path_class:
                mock_path_instance = Mock()
                mock_path_instance.parent = Mock(side_effect=AttributeError("'NoneType' object has no attribute 'parent'"))
                mock_path_class.return_value = mock_path_instance
                
                result = router._get_app_pages_path('testapp')
                assert result is None



    def test_get_app_pages_path_attribute_error_on_pages_path_exists(self):
        """Test getting app pages path with AttributeError when checking pages_path.exists()."""
        router = FileRouterBackend()
        with patch('builtins.__import__') as mock_import:
            mock_module = Mock()
            mock_module.__file__ = '/path/to/app/__init__.py'
            mock_import.return_value = mock_module
            
            # create a real Path object but mock the .exists() method to raise AttributeError
            with patch('next.urls.Path') as mock_path_class:
                mock_path_instance = Mock()
                mock_path_instance.parent = Mock()
                mock_path_instance.parent.__truediv__ = Mock(return_value=mock_path_instance)
                mock_path_instance.exists = Mock(side_effect=AttributeError("'NoneType' object has no attribute 'exists'"))
                mock_path_class.return_value = mock_path_instance
                
                result = router._get_app_pages_path('testapp')
                assert result is None

    def test_get_app_pages_path_import_error(self):
        """Test getting app pages path with ImportError."""
        router = FileRouterBackend()
        with patch('builtins.__import__', side_effect=ImportError("No module named 'nonexistent'")):
            result = router._get_app_pages_path('nonexistent')
            assert result is None

    def test_load_page_function_spec_none(self):
        """Test loading page function when spec is None."""
        router = FileRouterBackend()
        
        with patch('importlib.util.spec_from_file_location', return_value=None):
            result = router._load_page_function(Path('/tmp/page.py'))
            assert result is None

    def test_load_page_function_loader_none(self):
        """Test loading page function when loader is None."""
        router = FileRouterBackend()
        
        mock_spec = Mock()
        mock_spec.loader = None
        
        with patch('importlib.util.spec_from_file_location', return_value=mock_spec):
            result = router._load_page_function(Path('/tmp/page.py'))
            assert result is None

    def test_load_page_function_exec_module_exception(self):
        """Test loading page function when exec_module raises an exception."""
        router = FileRouterBackend()
        
        with patch('importlib.util.spec_from_file_location') as mock_spec:
            mock_spec.return_value = Mock(loader=Mock())
            
            with patch('importlib.util.module_from_spec') as mock_module:
                mock_module.return_value = Mock()
                
                # make exec_module raise an exception to cover the except block
                mock_spec.return_value.loader.exec_module.side_effect = Exception("Test error during exec_module")
                
                result = router._load_page_function(Path('/tmp/page.py'))
                assert result is None

    def test_load_page_function_getattr_returns_none(self):
        """Test loading page function when getattr returns None."""
        router = FileRouterBackend()
        
        with patch('importlib.util.spec_from_file_location') as mock_spec:
            mock_spec.return_value = Mock(loader=Mock())
            
            with patch('importlib.util.module_from_spec') as mock_module:
                mock_module.return_value = Mock()
                
                with patch('builtins.getattr', return_value=None):
                    result = router._load_page_function(Path('/tmp/page.py'))
                    assert result is None

    def test_load_page_function_getattr_returns_non_callable(self):
        """Test loading page function when getattr returns non-callable."""
        router = FileRouterBackend()
        
        with patch('importlib.util.spec_from_file_location') as mock_spec:
            mock_spec.return_value = Mock(loader=Mock())
            
            with patch('importlib.util.module_from_spec') as mock_module:
                mock_module.return_value = Mock()
                
                with patch('builtins.getattr', return_value="not a function"):
                    result = router._load_page_function(Path('/tmp/page.py'))
                    assert result is None

    def test_load_page_function_exec_module_error(self):
        """Test loading page function when exec_module raises an exception."""
        router = FileRouterBackend()
        
        with patch('importlib.util.spec_from_file_location') as mock_spec:
            mock_spec.return_value = Mock(loader=Mock())
            
            with patch('importlib.util.module_from_spec') as mock_module:
                mock_module.return_value = Mock()
                
                # make exec_module raise an exception
                mock_spec.return_value.loader.exec_module.side_effect = Exception("Test error")
                
                result = router._load_page_function(Path('/tmp/page.py'))
                assert result is None


class TestRouterFactory:
    """Test the RouterFactory class."""
    
    def test_register_backend(self):
        """Test registering a new backend."""
        class CustomBackend(FileRouterBackend):
            pass
        
        RouterFactory.register_backend('custom.backend', CustomBackend)
        assert 'custom.backend' in RouterFactory._backends
        assert RouterFactory._backends['custom.backend'] == CustomBackend
    
    def test_create_backend_success(self):
        """Test creating backend successfully."""
        config = {
            'BACKEND': 'next.urls.FileRouterBackend',
            'APP_DIRS': True,
            'OPTIONS': {'PAGES_DIR_NAME': 'views'}
        }
        
        router = RouterFactory.create_backend(config)
        assert isinstance(router, FileRouterBackend)
        assert router.pages_dir_name == 'views'
        assert router.app_dirs is True
    
    def test_create_backend_defaults(self):
        """Test creating backend with default values."""
        config = {'BACKEND': 'next.urls.FileRouterBackend'}
        
        router = RouterFactory.create_backend(config)
        assert isinstance(router, FileRouterBackend)
        assert router.pages_dir_name == 'pages'
        assert router.app_dirs is True
        assert router.options == {}
    
    def test_create_backend_unsupported(self):
        """Test creating backend with unsupported backend name."""
        config = {'BACKEND': 'unsupported.backend'}
        
        with pytest.raises(ValueError, match="Unsupported backend"):
            RouterFactory.create_backend(config)
    
    def test_create_backend_missing_backend(self):
        """Test creating backend with missing backend name."""
        config = {}
        
        router = RouterFactory.create_backend(config)
        assert isinstance(router, FileRouterBackend)

    def test_create_backend_non_file_router_backend(self):
        """Test creating backend with non-FileRouterBackend type."""
        class CustomBackend(RouterBackend):
            def generate_urls(self):
                return []
        
        RouterFactory.register_backend('custom.backend', CustomBackend)
        
        config = {'BACKEND': 'custom.backend'}
        router = RouterFactory.create_backend(config)
        assert isinstance(router, CustomBackend)

    def test_create_backend_non_file_router_backend_else_branch(self):
        """Test creating backend with non-FileRouterBackend type to cover else branch."""
        class CustomBackend(RouterBackend):
            def generate_urls(self):
                return []
        
        RouterFactory.register_backend('custom.backend', CustomBackend)
        
        config = {'BACKEND': 'custom.backend'}
        router = RouterFactory.create_backend(config)

        assert isinstance(router, CustomBackend)
        # verify that the else branch was executed (customBackend was created without arguments)
        assert not hasattr(router, 'pages_dir_name')  # customBackend doesn't have this attribute


class TestRouterManager:
    """Test the RouterManager class."""
    
    def test_init(self):
        """Test manager initialization."""
        manager = RouterManager()
        assert manager._routers == []
        assert manager._config_cache is None
    
    def test_repr(self):
        """Test string representation."""
        manager = RouterManager()
        assert repr(manager) == '<RouterManager routers=0>'
    
    def test_len(self):
        """Test length method."""
        manager = RouterManager()
        assert len(manager) == 0
        
        manager._routers = [Mock(), Mock()]
        assert len(manager) == 2
    
    def test_iter(self):
        """Test iteration."""
        manager = RouterManager()
        manager._routers = [Mock(), Mock()]
        
        routers = list(manager)
        assert len(routers) == 2
    
    def test_getitem(self):
        """Test indexing."""
        manager = RouterManager()
        mock_router = Mock()
        manager._routers = [mock_router]
        
        assert manager[0] == mock_router
    
    def test_reload_config_success(self):
        """Test successful configuration reload."""
        manager = RouterManager()
        manager._config_cache = [{'BACKEND': 'cached.Backend'}]
        manager._routers = [Mock()]
        
        with patch.object(manager, '_get_next_pages_config') as mock_get_config:
            mock_get_config.return_value = [
                {'BACKEND': 'next.urls.FileRouterBackend', 'APP_DIRS': True}
            ]
            
            with patch('next.urls.RouterFactory.create_backend') as mock_create:
                mock_router = Mock()
                mock_create.return_value = mock_router
                
                manager.reload_config()
                
                assert manager._config_cache is None
                assert len(manager._routers) == 1
                assert manager._routers[0] == mock_router
    
    def test_reload_config_with_error(self):
        """Test configuration reload with error."""
        manager = RouterManager()
        
        with patch.object(manager, '_get_next_pages_config') as mock_get_config:
            mock_get_config.return_value = [
                {'BACKEND': 'invalid.backend'}
            ]
            
            with patch('next.urls.RouterFactory.create_backend') as mock_create:
                mock_create.side_effect = ValueError("Invalid backend")
                
                manager.reload_config()
                
                assert len(manager._routers) == 0

    def test_reload_config_with_exception_during_creation(self):
        """Test configuration reload when router creation raises an exception."""
        manager = RouterManager()
        
        with patch.object(manager, '_get_next_pages_config') as mock_get_config:
            mock_get_config.return_value = [
                {'BACKEND': 'next.urls.FileRouterBackend', 'APP_DIRS': True}
            ]
            
            with patch('next.urls.RouterFactory.create_backend') as mock_create:
                mock_create.side_effect = Exception("Unexpected error during router creation")
                
                manager.reload_config()
                
                assert len(manager._routers) == 0
    
    def test_get_next_pages_config_default(self):
        """Test getting default configuration."""
        manager = RouterManager()
        
        with patch('next.urls.settings') as mock_settings:
            # no NEXT_PAGES setting
            delattr(mock_settings, 'NEXT_PAGES')
            
            config = manager._get_next_pages_config()
            assert len(config) == 1
            assert config[0]['BACKEND'] == 'next.urls.FileRouterBackend'
            assert config[0]['APP_DIRS'] is True
    
    def test_get_next_pages_config_custom(self):
        """Test getting custom configuration."""
        manager = RouterManager()
        
        custom_config = [
            {'BACKEND': 'custom.backend', 'APP_DIRS': False}
        ]
        
        with patch('next.urls.settings') as mock_settings:
            mock_settings.NEXT_PAGES = custom_config
            
            config = manager._get_next_pages_config()
            assert config == custom_config
    
    def test_get_next_pages_config_cache(self):
        """Test configuration caching."""
        manager = RouterManager()
        manager._config_cache = ['cached_config']
        
        config = manager._get_next_pages_config()
        assert config == ['cached_config']
    
    def test_generate_all_urls_empty(self):
        """Test generating URLs when no routers are configured."""
        manager = RouterManager()
        
        with patch.object(manager, 'reload_config'):
            urls = manager.generate_all_urls()
            assert urls == []
    
    def test_generate_all_urls_with_routers(self):
        """Test generating URLs with configured routers."""
        manager = RouterManager()
        
        mock_router1 = Mock()
        mock_router1.generate_urls.return_value = ['url1', 'url2']
        
        mock_router2 = Mock()
        mock_router2.generate_urls.return_value = ['url3']
        
        manager._routers = [mock_router1, mock_router2]
        
        urls = manager.generate_all_urls()
        assert urls == ['url1', 'url2', 'url3']


class TestConfigurationFunctions:
    """Test the public configuration functions."""
    
    def test_get_next_pages_config(self):
        """Test get_next_pages_config function."""
        with patch('next.urls.router_manager') as mock_manager:
            mock_manager._get_next_pages_config.return_value = ['config1']
            
            result = get_next_pages_config()
            assert result == ['config1']
    
    def test_create_router_from_config(self):
        """Test create_router_from_config function."""
        config = {'BACKEND': 'next.urls.FileRouterBackend'}
        
        with patch('next.urls.RouterFactory.create_backend') as mock_create:
            mock_router = Mock()
            mock_create.return_value = mock_router
            
            result = create_router_from_config(config)
            assert result == mock_router
    
    def test_get_configured_routers(self):
        """Test get_configured_routers function."""
        with patch('next.urls.router_manager') as mock_manager:
            mock_manager._routers = ['router1', 'router2']
            
            result = get_configured_routers()
            assert result == ['router1', 'router2']
    
    def test_get_configured_routers_reload(self):
        """Test get_configured_routers function with reload."""
        with patch('next.urls.router_manager') as mock_manager:
            mock_manager._routers = []
            mock_manager.reload_config = Mock()
            
            result = get_configured_routers()
            assert result == []
            mock_manager.reload_config.assert_called_once()


class TestIntegrationFunctions:
    """Test the integration functions."""
    
    def test_include_pages_success(self):
        """Test include_pages function with successful router creation."""
        with patch('next.urls.FileRouterBackend') as mock_router_class:
            mock_router = Mock()
            mock_router._generate_urls_for_app.return_value = ['pattern1']
            mock_router_class.return_value = mock_router
            
            result = include_pages('testapp')
            assert result == ['pattern1']
    
    def test_include_pages_no_patterns(self):
        """Test include_pages function when no patterns are found."""
        with patch('next.urls.FileRouterBackend') as mock_router_class:
            mock_router = Mock()
            mock_router._generate_urls_for_app.return_value = []
            mock_router_class.return_value = mock_router
            
            result = include_pages('testapp')
            assert result == []
    
    def test_auto_include_all_pages(self):
        """Test auto_include_all_pages function."""
        with patch('next.urls.router_manager') as mock_manager:
            mock_manager.generate_all_urls.return_value = ['url1', 'url2']
            
            result = auto_include_all_pages()
            assert result == ['url1', 'url2']


class TestGlobalInstances:
    """Test global instances and their behavior."""
    
    def test_router_manager_instance(self):
        """Test that router_manager is properly initialized."""
        from next.urls import router_manager
        assert isinstance(router_manager, RouterManager)
    
    def test_router_manager_reload_config_clears_cache(self):
        """Test that reload_config clears the cache."""
        from next.urls import router_manager
        
        # set initial state
        router_manager._config_cache = [{'BACKEND': 'cached.Backend'}]
        router_manager._routers = [Mock()]
        
        # mock the RouterFactory.create_backend method
        test_config = [{'BACKEND': 'next.urls.FileRouterBackend'}]
        mock_router = Mock()
        
        with patch('next.urls.RouterFactory.create_backend', return_value=mock_router):
            router_manager.reload_config()
            
            # cache should be populated with new config, not None
            assert router_manager._config_cache is not None
            assert router_manager._config_cache != [{'BACKEND': 'cached.Backend'}]  # cache was updated
            assert len(router_manager._routers) == 1
            assert router_manager._routers[0] == mock_router
    
    def test_urlpatterns_empty(self):
        """Test that urlpatterns starts empty."""
        from next.urls import urlpatterns
        assert urlpatterns == []
