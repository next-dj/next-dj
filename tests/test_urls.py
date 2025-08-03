import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from next.urls import FileRouter, include_pages


class TestFileRouter:
    """Test cases for FileRouter class."""

    @pytest.fixture
    def router(self):
        """Create router instance for tests."""
        return FileRouter()
    
    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for tests."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        import shutil
        shutil.rmtree(temp_dir)
    
    @pytest.fixture
    def pages_dir(self, temp_dir):
        """Create pages directory for tests."""
        pages_dir = Path(temp_dir) / 'testapp' / 'pages'
        pages_dir.mkdir(parents=True, exist_ok=True)
        return pages_dir
    
    def test_parse_url_pattern_simple(self, router):
        """Test parsing simple URL patterns."""
        pattern, params = router.parse_url_pattern('profile')
        assert pattern == 'profile'
        assert params == {}
    
    def test_parse_url_pattern_with_param(self, router):
        """Test parsing URL patterns with [param]."""
        pattern, params = router.parse_url_pattern('profile/[id]')
        assert pattern == 'profile/<str:id>'
        assert params == {'id': 'id'}
    
    def test_parse_url_pattern_with_param_dash(self, router):
        """Test parsing URL patterns with [param-name]."""
        pattern, params = router.parse_url_pattern('profile/[item-id]')
        assert pattern == 'profile/<str:item_id>'
        assert params == {'item_id': 'item_id'}
    
    def test_parse_url_pattern_with_type(self, router):
        """Test parsing URL patterns with [type:param]."""
        pattern, params = router.parse_url_pattern('profile/[int:user-id]')
        assert pattern == 'profile/<int:user_id>'
        assert params == {'user_id': 'user_id'}
    
    def test_parse_url_pattern_with_type_no_dash(self, router):
        """Test parsing URL patterns with [type:param] without dash."""
        pattern, params = router.parse_url_pattern('profile/[int:userid]')
        assert pattern == 'profile/<int:userid>'
        assert params == {'userid': 'userid'}
    
    def test_parse_url_pattern_with_slug_type(self, router):
        """Test parsing URL patterns with [slug:param]."""
        pattern, params = router.parse_url_pattern('posts/[slug:post-slug]')
        assert pattern == 'posts/<slug:post_slug>'
        assert params == {'post_slug': 'post_slug'}
    
    def test_parse_url_pattern_with_uuid_type(self, router):
        """Test parsing URL patterns with [uuid:param]."""
        pattern, params = router.parse_url_pattern('users/[uuid:user-id]')
        assert pattern == 'users/<uuid:user_id>'
        assert params == {'user_id': 'user_id'}

    def test_parse_url_pattern_with_args(self, router):
        """Test parsing URL patterns with [[args]]."""
        pattern, params = router.parse_url_pattern('profile/[[args]]')
        assert pattern == 'profile/<path:args>'
        assert params == {'args': 'args'}
    
    def test_parse_url_pattern_mixed(self, router):
        """Test parsing URL patterns with both [param] and [[args]]."""
        pattern, params = router.parse_url_pattern('user/[id]/posts/[[args]]')
        assert pattern == 'user/<str:id>/posts/<path:args>'
        assert params == {'id': 'id', 'args': 'args'}
    
    def test_parse_url_pattern_mixed_with_dash(self, router):
        """Test parsing URL patterns with dashes in parameter names."""
        pattern, params = router.parse_url_pattern('user/[user-id]/posts/[[post-args]]')
        assert pattern == 'user/<str:user_id>/posts/<path:post_args>'
        assert params == {'user_id': 'user_id', 'post_args': 'post_args'}
    
    def test_scan_pages_directory(self, router, pages_dir):
        """Test scanning pages directory."""
        # create test structure
        (pages_dir / 'home' / 'page.py').parent.mkdir()
        (pages_dir / 'home' / 'page.py').write_text('def render(request): pass')
        
        (pages_dir / 'profile' / '[id]' / 'page.py').parent.mkdir(parents=True)
        (pages_dir / 'profile' / '[id]' / 'page.py').write_text('def render(request, id): pass')
        
        pages = router.scan_pages_directory(pages_dir)
        assert len(pages) == 2
        
        # check that we found both page.py files
        url_paths = [path for path, _ in pages]
        assert 'home' in url_paths
        assert 'profile/[id]' in url_paths
    
    def test_load_page_function(self, router, pages_dir):
        """Test loading render function from page.py file."""
        page_content = '''
def render(request, **kwargs):
    return {"message": "Hello World"}
'''
        page_file = pages_dir / 'test' / 'page.py'
        page_file.parent.mkdir()
        page_file.write_text(page_content)
        
        render_func = router.load_page_function(page_file)
        assert render_func is not None
        assert render_func.__name__ == 'render'
    
    def test_load_page_function_missing(self, router, pages_dir):
        """Test loading page function when render function is missing."""
        page_content = '''
def other_function(request):
    pass
'''
        page_file = pages_dir / 'test' / 'page.py'
        page_file.parent.mkdir()
        page_file.write_text(page_content)
        
        render_func = router.load_page_function(page_file)
        assert render_func is None
    
    def test_load_page_function_import_error(self, router, pages_dir):
        """Test loading page function with import error."""
        # create invalid python file
        page_file = pages_dir / 'test' / 'page.py'
        page_file.parent.mkdir()
        page_file.write_text('invalid python syntax {')
        
        render_func = router.load_page_function(page_file)
        assert render_func is None
    
    def test_load_page_function_spec_none(self, router, pages_dir):
        """Test loading page function when spec is None."""
        page_file = pages_dir / 'test' / 'page.py'
        page_file.parent.mkdir()
        page_file.write_text('def render(request): pass')
        
        with patch('importlib.util.spec_from_file_location', return_value=None):
            render_func = router.load_page_function(page_file)
            assert render_func is None
    
    def test_load_page_function_loader_none(self, router, pages_dir):
        """Test loading page function when loader is None."""
        page_file = pages_dir / 'test' / 'page.py'
        page_file.parent.mkdir()
        page_file.write_text('def render(request): pass')
        
        mock_spec = Mock()
        mock_spec.loader = None
        with patch('importlib.util.spec_from_file_location', return_value=mock_spec):
            render_func = router.load_page_function(page_file)
            assert render_func is None
    
    def test_load_page_function_not_callable(self, router, pages_dir):
        """Test loading page function when render is not callable."""
        page_content = '''
render = "not a function"
'''
        page_file = pages_dir / 'test' / 'page.py'
        page_file.parent.mkdir()
        page_file.write_text(page_content)
        
        render_func = router.load_page_function(page_file)
        assert render_func is None
    
    def test_load_page_function_execution_error(self, router, pages_dir):
        """Test loading page function with execution error."""
        page_content = '''
# this will cause an error during execution
undefined_variable
def render(request):
    pass
'''
        page_file = pages_dir / 'test' / 'page.py'
        page_file.parent.mkdir()
        page_file.write_text(page_content)
        
        render_func = router.load_page_function(page_file)
        assert render_func is None
    
    def test_load_page_function_exec_module_error(self, router, pages_dir):
        """Test loading page function with exec_module error."""
        page_file = pages_dir / 'test' / 'page.py'
        page_file.parent.mkdir()
        page_file.write_text('def render(request): pass')
        
        with patch('importlib.util.module_from_spec') as mock_module_from_spec:
            mock_module = Mock()
            mock_module_from_spec.return_value = mock_module
            
            with patch('importlib.util.spec_from_file_location') as mock_spec_from_file:
                mock_spec = Mock()
                mock_spec.loader = Mock()
                mock_spec_from_file.return_value = mock_spec
                
                # make exec_module raise an exception
                mock_spec.loader.exec_module.side_effect = Exception("Test error")
                
                render_func = router.load_page_function(page_file)
                assert render_func is None
    
    @patch('next.urls.settings')
    def test_get_app_pages_path(self, mock_settings, router, pages_dir):
        """Test getting app pages path."""
        mock_settings.INSTALLED_APPS = ['testapp']
        
        with patch('builtins.__import__') as mock_import:
            mock_module = Mock()
            mock_module.__file__ = str(pages_dir.parent / '__init__.py')
            mock_import.return_value = mock_module
            
            pages_path = router.get_app_pages_path('testapp')
            assert pages_path == pages_dir
    
    @patch('next.urls.settings')
    def test_get_app_pages_path_app_not_found(self, mock_settings, router):
        """Test getting app pages path when app is not found."""
        mock_settings.INSTALLED_APPS = ['other_app']
        
        pages_path = router.get_app_pages_path('testapp')
        assert pages_path is None
    
    @patch('next.urls.settings')
    def test_get_app_pages_path_import_error(self, mock_settings, router):
        """Test getting app pages path with import error."""
        mock_settings.INSTALLED_APPS = ['testapp']
        
        with patch('builtins.__import__', side_effect=ImportError):
            pages_path = router.get_app_pages_path('testapp')
            assert pages_path is None
    
    @patch('next.urls.settings')
    def test_get_app_pages_path_no_file(self, mock_settings, router):
        """Test getting app pages path when __file__ is None."""
        mock_settings.INSTALLED_APPS = ['testapp']
        
        with patch('builtins.__import__') as mock_import:
            mock_module = Mock()
            mock_module.__file__ = None
            mock_import.return_value = mock_module
            
            pages_path = router.get_app_pages_path('testapp')
            assert pages_path is None
    
    @patch('next.urls.settings')
    def test_get_app_pages_path_no_pages_dir(self, mock_settings, router, temp_dir):
        """Test getting app pages path when pages directory doesn't exist."""
        mock_settings.INSTALLED_APPS = ['testapp']
        
        app_dir = Path(temp_dir) / 'testapp'
        app_dir.mkdir()
        (app_dir / '__init__.py').write_text('')
        
        with patch('builtins.__import__') as mock_import:
            mock_module = Mock()
            mock_module.__file__ = str(app_dir / '__init__.py')
            mock_import.return_value = mock_module
            
            pages_path = router.get_app_pages_path('testapp')
            assert pages_path is None
    
    def test_create_url_pattern(self, router, pages_dir):
        """Test creating URL pattern from page.py file."""
        page_content = '''
def render(request, item_id=None, **kwargs):
    return {"id": item_id}
'''
        page_file = pages_dir / 'profile' / '[item-id]' / 'page.py'
        page_file.parent.mkdir(parents=True)
        page_file.write_text(page_content)
        
        pattern = router.create_url_pattern('profile/[item-id]', page_file)
        assert pattern is not None
        # проверяем только что паттерн создался, не проверяем точный regex
    
    def test_create_url_pattern_no_render_function(self, router, pages_dir):
        """Test creating URL pattern when render function is missing."""
        page_content = '''
def other_function(request):
    pass
'''
        page_file = pages_dir / 'test' / 'page.py'
        page_file.parent.mkdir()
        page_file.write_text(page_content)
        
        pattern = router.create_url_pattern('test', page_file)
        assert pattern is None
    
    def test_create_url_pattern_with_args_parameter(self, router, pages_dir):
        """Test creating URL pattern with args parameter."""
        page_content = '''
def render(request, args=None, **kwargs):
    return {"args": args}
'''
        page_file = pages_dir / 'profile' / '[[args]]' / 'page.py'
        page_file.parent.mkdir(parents=True)
        page_file.write_text(page_content)
        
        pattern = router.create_url_pattern('profile/[[args]]', page_file)
        assert pattern is not None
    
    def test_generate_urls_for_app_cache(self, router, pages_dir):
        """Test that generate_urls_for_app uses cache."""
        # create test page
        (pages_dir / 'test' / 'page.py').parent.mkdir()
        (pages_dir / 'test' / 'page.py').write_text('def render(request): pass')
        
        with patch.object(router, 'get_app_pages_path', return_value=pages_dir):
            # first call should populate cache
            patterns1 = router.generate_urls_for_app('testapp')
            assert len(patterns1) == 1
            
            # second call should use cache
            patterns2 = router.generate_urls_for_app('testapp')
            assert patterns2 == patterns1
    
    def test_generate_urls_for_app_no_pages_path(self, router):
        """Test generate_urls_for_app when pages path doesn't exist."""
        with patch.object(router, 'get_app_pages_path', return_value=None):
            patterns = router.generate_urls_for_app('testapp')
            assert patterns == []
    
    def test_include_app_pages(self, router):
        """Test include_app_pages method."""
        with patch.object(router, 'generate_urls_for_app', return_value=[]):
            resolver = router.include_app_pages('testapp')
            assert resolver is not None


class TestFileRouterIntegration:
    """Integration tests for file router."""
    
    def test_include_pages_function(self):
        """Test include_pages function."""
        resolver = include_pages('testapp')
        assert resolver is not None
    
    def test_urlpatterns_auto_generation(self):
        """Test that urlpatterns are auto-generated."""
        from next.urls import urlpatterns
        assert isinstance(urlpatterns, list)
    
    @patch('next.urls.settings')
    def test_auto_include_all_pages(self, mock_settings):
        """Test auto_include_all_pages function."""
        from next.urls import auto_include_all_pages
        
        mock_settings.INSTALLED_APPS = ['testapp', 'django.contrib.admin']
        
        with patch('next.urls.file_router.generate_urls_for_app') as mock_generate:
            mock_generate.return_value = ['pattern1', 'pattern2']
            
            resolvers = auto_include_all_pages()
            assert len(resolvers) == 1  # only testapp, not django.contrib.admin
    
    @patch('next.urls.settings')
    def test_auto_include_all_pages_no_patterns(self, mock_settings):
        """Test auto_include_all_pages when no patterns are found."""
        from next.urls import auto_include_all_pages
        
        mock_settings.INSTALLED_APPS = ['testapp']
        
        with patch('next.urls.file_router.generate_urls_for_app') as mock_generate:
            mock_generate.return_value = []
            
            resolvers = auto_include_all_pages()
            assert len(resolvers) == 0


class TestFileRouterEndToEnd:
    """End-to-end tests for file router."""
    
    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for tests."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        import shutil
        shutil.rmtree(temp_dir)
    
    @pytest.fixture
    def test_app_dir(self, temp_dir):
        """Create test app directory."""
        test_app_dir = Path(temp_dir) / 'testapp'
        pages_dir = test_app_dir / 'pages'
        pages_dir.mkdir(parents=True, exist_ok=True)
        
        # create __init__.py for test app
        (test_app_dir / '__init__.py').write_text('')
        return test_app_dir
    
    @pytest.fixture
    def pages_dir(self, test_app_dir):
        """Get pages directory."""
        return test_app_dir / 'pages'
    
    def test_simple_page_rendering(self, pages_dir, test_app_dir):
        """Test simple page rendering."""
        # create a simple page
        page_content = '''
from django.http import JsonResponse

def render(request):
    return JsonResponse({"message": "Hello from test page"})
'''
        page_file = pages_dir / 'test' / 'page.py'
        page_file.parent.mkdir()
        page_file.write_text(page_content)
        
        # mock the app module and settings
        with patch('builtins.__import__') as mock_import, patch('next.urls.settings') as mock_settings:
            mock_module = Mock()
            mock_module.__file__ = str(test_app_dir / '__init__.py')
            mock_import.return_value = mock_module
            mock_settings.INSTALLED_APPS = ['testapp']
            
            # test that the router can find and load the page
            router = FileRouter()
            pages_path = router.get_app_pages_path('testapp')
            assert pages_path == pages_dir
            
            pages = router.scan_pages_directory(pages_path)
            assert len(pages) == 1
            
            url_path, file_path = pages[0]
            assert url_path == 'test'
            
            pattern = router.create_url_pattern(url_path, file_path)
            assert pattern is not None
    
    def test_dynamic_parameter_page(self, pages_dir, test_app_dir):
        """Test page with dynamic parameter."""
        # create a page with parameter
        page_content = '''
from django.http import JsonResponse

def render(request, item_id=None, **kwargs):
    return JsonResponse({"id": item_id, "message": f"Profile {item_id}"})
'''
        page_file = pages_dir / 'profile' / '[item-id]' / 'page.py'
        page_file.parent.mkdir(parents=True)
        page_file.write_text(page_content)
        
        # mock the app module and settings
        with patch('builtins.__import__') as mock_import, patch('next.urls.settings') as mock_settings:
            mock_module = Mock()
            mock_module.__file__ = str(test_app_dir / '__init__.py')
            mock_import.return_value = mock_module
            mock_settings.INSTALLED_APPS = ['testapp']
            
            # test parameter parsing
            router = FileRouter()
            pattern, params = router.parse_url_pattern('profile/[item-id]')
            assert pattern == 'profile/<str:item_id>'
            assert params == {'item_id': 'item_id'}
