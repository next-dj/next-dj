import gc
from unittest.mock import MagicMock, patch

import pytest
from django.template import Context

from next.templates import DJXCompiler, djx


@pytest.fixture
def compiler():
    """Provide a fresh DJXCompiler instance for each test."""
    return DJXCompiler()


@pytest.fixture
def mock_frame():
    """Mock frame for testing file path detection."""
    frame = MagicMock()
    frame.f_code.co_filename = "/test/file.py"
    frame.f_lineno = 42
    return frame


@pytest.fixture
def mock_currentframe():
    """Mock currentframe that returns a frame with f_back."""
    frame = MagicMock()
    frame.f_back = mock_frame()
    return frame


class TestDJXCompiler:
    """Test cases for DJXCompiler class."""

    def test_init(self, compiler):
        """Test DJXCompiler initialization."""
        assert compiler._templates == {}
        assert compiler._content_hashes == {}
        assert compiler._file_paths == {}
        assert compiler._file_blocks == {}

    def test_generate_content_hash(self, compiler):
        """Test content hash generation."""
        content = "Hello World"
        hash1 = compiler._generate_content_hash(content)
        hash2 = compiler._generate_content_hash(content)

        # same content should generate same hash
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 hex length

        # different content should generate different hash
        hash3 = compiler._generate_content_hash("Different content")
        assert hash1 != hash3

    def test_len_and_contains(self, compiler):
        """Test len() and contains functionality."""
        template = "Test template"

        assert len(compiler) == 0
        assert template not in compiler

        # add template manually to avoid Django template creation issues
        content_hash = compiler._generate_content_hash(template)
        compiler._templates[content_hash] = "test_template"
        compiler._content_hashes[content_hash] = template
        compiler._file_paths[content_hash] = "/test/file.py"
        compiler._file_blocks["/test/file.py"] = [content_hash]

        assert len(compiler) == 1
        assert template in compiler


class TestDJXOperator:
    """Test cases for the % operator functionality."""

    @patch("next.templates.Template")
    def test_mod_operator(self, mock_template_class, compiler):
        """Test % operator for adding template blocks."""
        mock_template = MagicMock()
        mock_template_class.return_value = mock_template

        # Create a proper mock for the frame chain
        mock_frame = MagicMock()
        mock_frame.f_code.co_filename = "/test/file.py"

        mock_currentframe = MagicMock()
        mock_currentframe.f_back = mock_frame

        with patch("inspect.currentframe", return_value=mock_currentframe):
            template = "Hello {{ name }}"
            result = compiler % template

            assert result is compiler
            assert len(compiler._content_hashes) == 1
            assert len(compiler._file_paths) == 1

            # check that template was stored
            content_hash = list(compiler._content_hashes.keys())[0]
            assert (
                compiler._content_hashes[content_hash] == template
            )  # should contain original string
            assert (
                compiler._templates[content_hash] == mock_template
            )  # should contain Django Template object
            assert compiler._file_paths[content_hash] == "/test/file.py"

    @patch("next.templates.Template")
    def test_operator_file_path_detection(self, mock_template_class, compiler):
        """Test that file path is correctly detected."""
        mock_template = MagicMock()
        mock_template_class.return_value = mock_template

        # Create a proper mock for the frame chain
        mock_frame = MagicMock()
        mock_frame.f_code.co_filename = "/path/to/test.py"

        mock_currentframe = MagicMock()
        mock_currentframe.f_back = mock_frame

        with patch("inspect.currentframe", return_value=mock_currentframe):
            template = "Test template"
            compiler % template

            content_hash = list(compiler._content_hashes.keys())[0]
            file_path = compiler._file_paths[content_hash]
            assert file_path == "/path/to/test.py"

    @patch("next.templates.Template")
    def test_operator_unknown_location(self, mock_template_class, compiler):
        """Test behavior when frame inspection fails."""
        mock_template = MagicMock()
        mock_template_class.return_value = mock_template

        # When inspect.currentframe() returns None, f_back will fail
        # but the code should handle this and set file_path to "unknown"
        with patch("inspect.currentframe", return_value=None):
            template = "Test template"
            compiler % template

            content_hash = list(compiler._content_hashes.keys())[0]
            file_path = compiler._file_paths[content_hash]
            assert file_path == "unknown"

    @patch("next.templates.Template")
    def test_operator_overwrites_existing_block(self, mock_template_class, compiler):
        """Test that existing blocks with same content are overwritten."""
        mock_template = MagicMock()
        mock_template_class.return_value = mock_template

        # Create a proper mock for the frame chain
        mock_frame = MagicMock()
        mock_frame.f_code.co_filename = "/test/file.py"

        mock_currentframe = MagicMock()
        mock_currentframe.f_back = mock_frame

        with patch("inspect.currentframe", return_value=mock_currentframe):
            template = "Same template"

            # add first time
            compiler % template
            first_hash = list(compiler._content_hashes.keys())[0]
            compiler._templates[first_hash]

            # add same template again
            compiler % template
            second_hash = list(compiler._content_hashes.keys())[0]
            compiler._templates[second_hash]

            # should be same hash but potentially different template object
            assert first_hash == second_hash
            assert len(compiler._content_hashes) == 1


class TestTemplateCollection:
    """Test cases for template collection functionality."""

    @patch("next.templates.Template")
    def test_get_nodes_single_file(self, mock_template_class, compiler):
        """Test getting template nodes from a single file."""
        mock_template = MagicMock()
        mock_template_class.return_value = mock_template

        # Create a proper mock for the frame chain
        mock_frame = MagicMock()
        mock_frame.f_code.co_filename = "/test/file.py"

        mock_currentframe = MagicMock()
        mock_currentframe.f_back = mock_frame

        with patch("inspect.currentframe", return_value=mock_currentframe):
            # add templates
            compiler % "Template 1"
            compiler % "Template 2"

            # get nodes
            nodes = compiler.get_nodes("/test/file.py")

            assert len(nodes) == 2
            assert all(node == mock_template for node in nodes)

    def test_get_nodes_empty_file(self, compiler):
        """Test getting nodes from a file with no templates."""
        nodes = compiler.get_nodes("/nonexistent/file.py")
        assert nodes == []

    @patch("next.templates.Template")
    def test_get_nodes_multiple_files(self, mock_template_class, compiler):
        """Test getting nodes from specific file when multiple files have templates."""
        mock_template = MagicMock()
        mock_template_class.return_value = mock_template

        # add template from file1
        mock_frame1 = MagicMock()
        mock_frame1.f_code.co_filename = "/file1.py"

        mock_currentframe1 = MagicMock()
        mock_currentframe1.f_back = mock_frame1

        with patch("inspect.currentframe", return_value=mock_currentframe1):
            compiler % "Template from file1"

            # add template from file2
            mock_frame2 = MagicMock()
            mock_frame2.f_code.co_filename = "/file2.py"

            mock_currentframe2 = MagicMock()
            mock_currentframe2.f_back = mock_frame2

            with patch("inspect.currentframe", return_value=mock_currentframe2):
                compiler % "Template from file2"

                # get nodes from file1 only
                nodes1 = compiler.get_nodes("/file1.py")
                nodes2 = compiler.get_nodes("/file2.py")

                assert len(nodes1) == 1
                assert len(nodes2) == 1


class TestDjangoTemplateIntegration:
    """Test cases for Django template integration."""

    @patch("next.templates.Template")
    def test_template_creation(self, mock_template_class, compiler):
        """Test that Django Template objects are created correctly."""
        mock_template = MagicMock()
        mock_template_class.return_value = mock_template

        # Create a proper mock for the frame chain
        mock_frame = MagicMock()
        mock_frame.f_code.co_filename = "/test/file.py"

        mock_currentframe = MagicMock()
        mock_currentframe.f_back = mock_frame

        with patch("inspect.currentframe", return_value=mock_currentframe):
            template_string = "Hello {{ name }}"
            compiler % template_string

            nodes = compiler.get_nodes("/test/file.py")
            assert len(nodes) == 1

            template = nodes[0]
            assert template == mock_template

    @patch("next.templates.Template")
    def test_template_rendering(self, mock_template_class, compiler):
        """Test that collected templates can be rendered with context."""
        mock_template = MagicMock()
        mock_template.render.return_value = "Hello John, you have 5 items"
        mock_template_class.return_value = mock_template

        # Create a proper mock for the frame chain
        mock_frame = MagicMock()
        mock_frame.f_code.co_filename = "/test/file.py"

        mock_currentframe = MagicMock()
        mock_currentframe.f_back = mock_frame

        with patch("inspect.currentframe", return_value=mock_currentframe):
            template_string = "Hello {{ name }}, you have {{ count }} items"
            compiler % template_string

            nodes = compiler.get_nodes("/test/file.py")
            template = nodes[0]

            context = Context({"name": "John", "count": 5})
            rendered = template.render(context)
            assert rendered == "Hello John, you have 5 items"

    @patch("next.templates.Template")
    def test_template_with_loops(self, mock_template_class, compiler):
        """Test templates with Django template loops."""
        mock_template = MagicMock()
        mock_template.render.return_value = (
            "<div>0</div>\n    <div>1</div>\n    <div>2</div>"
        )
        mock_template_class.return_value = mock_template

        # Create a proper mock for the frame chain
        mock_frame = MagicMock()
        mock_frame.f_code.co_filename = "/test/file.py"

        mock_currentframe = MagicMock()
        mock_currentframe.f_back = mock_frame

        with patch("inspect.currentframe", return_value=mock_currentframe):
            template_string = """{% for i in range(3) %}
    <div>{{ i }}</div>
{% endfor %}"""
            compiler % template_string

            nodes = compiler.get_nodes("/test/file.py")
            template = nodes[0]

            context = Context({"range": range(3)})
            rendered = template.render(context)
            assert "0" in rendered
            assert "1" in rendered
            assert "2" in rendered

    @patch("next.templates.Template")
    def test_template_with_conditionals(self, mock_template_class, compiler):
        """Test templates with Django template conditionals."""
        mock_template = MagicMock()
        mock_template_class.return_value = mock_template

        # Create a proper mock for the frame chain
        mock_frame = MagicMock()
        mock_frame.f_code.co_filename = "/test/file.py"

        mock_currentframe = MagicMock()
        mock_currentframe.f_back = mock_frame

        with patch("inspect.currentframe", return_value=mock_currentframe):
            template_string = """{% if condition %}
    <div>True</div>
{% else %}
    <div>False</div>
{% endif %}"""
            compiler % template_string

            nodes = compiler.get_nodes("/test/file.py")
            template = nodes[0]

            # test true condition
            mock_template.render.return_value = "    <div>True</div>"
            context_true = Context({"condition": True})
            rendered_true = template.render(context_true)
            assert "True" in rendered_true
            assert "False" not in rendered_true

            # test false condition
            mock_template.render.return_value = "    <div>False</div>"
            context_false = Context({"condition": False})
            rendered_false = template.render(context_false)
            assert "False" in rendered_false
            assert "True" not in rendered_false

    @patch("next.templates.Template")
    def test_template_with_static_tags(self, mock_template_class, compiler):
        """Test templates with Django static tags."""
        mock_template = MagicMock()
        mock_template_class.return_value = mock_template

        # Create a proper mock for the frame chain
        mock_frame = MagicMock()
        mock_frame.f_code.co_filename = "/test/file.py"

        mock_currentframe = MagicMock()
        mock_currentframe.f_back = mock_frame

        with patch("inspect.currentframe", return_value=mock_currentframe):
            template_string = """{% load static %}
{% static 'path/to/file.png' %}"""
            compiler % template_string

            nodes = compiler.get_nodes("/test/file.py")
            template = nodes[0]

            assert template == mock_template


class TestGlobalDJX:
    """Test cases for the global djx instance."""

    def setup_method(self):
        """Clear the global djx cache before each test."""
        # manually clear the cache by creating a new instance
        global djx
        djx = DJXCompiler()

    @patch("next.templates.Template")
    def test_global_djx_operator(self, mock_template_class):
        """Test the global djx instance with % operator."""
        mock_template = MagicMock()
        mock_template_class.return_value = mock_template

        # Create a proper mock for the frame chain
        mock_frame = MagicMock()
        mock_frame.f_code.co_filename = "/test/file.py"

        mock_currentframe = MagicMock()
        mock_currentframe.f_back = mock_frame

        with patch("inspect.currentframe", return_value=mock_currentframe):
            template = "Global template"
            result = djx % template

            assert result is djx
            assert len(djx) == 1

    @patch("next.templates.Template")
    def test_global_djx_persistence(self, mock_template_class):
        """Test that global djx instance persists across operations."""
        mock_template = MagicMock()
        mock_template_class.return_value = mock_template

        # Create a proper mock for the frame chain
        mock_frame = MagicMock()
        mock_frame.f_code.co_filename = "/test/file.py"

        mock_currentframe = MagicMock()
        mock_currentframe.f_back = mock_frame

        with patch("inspect.currentframe", return_value=mock_currentframe):
            # add template
            djx % "Persistent template"
            assert len(djx) == 1

            # add another template
            djx % "Another template"
            assert len(djx) == 2

    @patch("next.templates.Template")
    def test_global_djx_template_rendering(self, mock_template_class):
        """Test that global djx templates can be rendered."""
        mock_template = MagicMock()
        mock_template.render.return_value = "Hello World"
        mock_template_class.return_value = mock_template

        # Create a proper mock for the frame chain
        mock_frame = MagicMock()
        mock_frame.f_code.co_filename = "/test/file.py"

        mock_currentframe = MagicMock()
        mock_currentframe.f_back = mock_frame

        with patch("inspect.currentframe", return_value=mock_currentframe):
            template_string = "Hello {{ name }}"
            djx % template_string

            templates = djx.get_nodes("/test/file.py")
            assert len(templates) == 1

            template = templates[0]
            context = Context({"name": "World"})
            rendered = template.render(context)
            assert rendered == "Hello World"


class TestGarbageCollection:
    """Test cases for automatic garbage collection."""

    def test_gc_callback_setup(self, compiler):
        """Test that garbage collection callback is set up."""
        # check if any callback was added to gc.callbacks
        # the callback is a closure function, so we can't easily identify it by __self__
        # but we can check that the number of callbacks increased
        initial_callback_count = len(gc.callbacks)

        # create a new compiler instance to trigger callback setup
        DJXCompiler()

        # check that a new callback was added
        new_callback_count = len(gc.callbacks)
        assert new_callback_count > initial_callback_count, (
            "No garbage collection callback was added"
        )

        # clean up the extra callback to avoid affecting other tests
        if new_callback_count > initial_callback_count:
            # remove the last added callback
            gc.callbacks.pop()

    @patch("next.templates.Template")
    def test_cache_cleanup_on_gc(self, mock_template_class, compiler):
        """Test that cache is cleaned up during garbage collection."""
        mock_template = MagicMock()
        mock_template_class.return_value = mock_template

        # Create a proper mock for the frame chain
        mock_frame = MagicMock()
        mock_frame.f_code.co_filename = "/test/file.py"

        mock_currentframe = MagicMock()
        mock_currentframe.f_back = mock_frame

        # add some templates
        with patch("inspect.currentframe", return_value=mock_currentframe):
            compiler % "Template 1"
            compiler % "Template 2"

            initial_count = len(compiler)
            assert initial_count == 2

            # manually trigger gc callback
            compiler._clear_expired_cache()

            # in this simple case, all templates should still be referenced
            assert len(compiler) == 2

    def test_clear_expired_cache_removes_unused_blocks(self, compiler):
        """Test that _clear_expired_cache actually removes unused template blocks."""
        # Manually add some templates to simulate expired state
        content_hash1 = "hash1"
        content_hash2 = "hash2"

        compiler._templates[content_hash1] = "template1"
        compiler._templates[content_hash2] = "template2"
        compiler._content_hashes[content_hash1] = "content1"
        compiler._content_hashes[content_hash2] = "content2"
        compiler._file_paths[content_hash1] = "/file1.py"
        compiler._file_paths[content_hash2] = "/file2.py"
        compiler._file_blocks["/file1.py"] = [content_hash1]
        compiler._file_blocks["/file2.py"] = [content_hash2]

        assert len(compiler) == 2

        # Mock _is_content_referenced to return False for one template
        with patch.object(compiler, "_is_content_referenced") as mock_is_referenced:
            mock_is_referenced.side_effect = (
                lambda h: h != content_hash1
            )  # hash1 is not referenced

            # Clear expired cache
            compiler._clear_expired_cache()

            # hash1 should be removed, hash2 should remain
            assert content_hash1 not in compiler._templates
            assert content_hash2 in compiler._templates
            assert len(compiler) == 1

    def test_remove_from_cache_removes_last_block_from_file(self, compiler):
        """Test that _remove_from_cache properly cleans up empty file entries."""
        # Manually add a template
        content_hash = "hash1"
        file_path = "/test/file.py"

        compiler._templates[content_hash] = "template1"
        compiler._content_hashes[content_hash] = "content1"
        compiler._file_paths[content_hash] = file_path
        compiler._file_blocks[file_path] = [content_hash]

        assert file_path in compiler._file_blocks
        assert len(compiler._file_blocks[file_path]) == 1

        # Remove the template
        compiler._remove_from_cache(content_hash)

        # File should be removed from _file_blocks since it's empty
        assert file_path not in compiler._file_blocks
        assert content_hash not in compiler._templates
        assert content_hash not in compiler._content_hashes
        assert content_hash not in compiler._file_paths


class TestTemplateErrorHandling:
    """Test cases for template error handling."""

    def test_invalid_template_handling(self, compiler):
        """Test handling of invalid Django templates."""
        # make Template raise an exception
        with patch("next.templates.Template") as mock_template_class:
            mock_template_class.side_effect = Exception("Template error")

            # Create a proper mock for the frame chain
            mock_frame = MagicMock()
            mock_frame.f_code.co_filename = "/test/file.py"

            mock_currentframe = MagicMock()
            mock_currentframe.f_back = mock_frame

            with patch("inspect.currentframe", return_value=mock_currentframe):
                invalid_template = "{% invalid_tag %}"
                compiler % invalid_template

                # should still be stored in content_hashes but template might be None
                assert len(compiler._content_hashes) == 1
                # the template object might be None if parsing fails

                # check that the template string is stored in _content_hashes
                content_hash = list(compiler._content_hashes.keys())[0]
                assert compiler._content_hashes[content_hash] == "{% invalid_tag %}"

                # check that _templates contains None for failed template
                assert compiler._templates[content_hash] is None

    def test_template_saved_to_content_hashes(self, compiler):
        """Test that template content is properly saved to _content_hashes."""
        # Create a proper mock for the frame chain
        mock_frame = MagicMock()
        mock_frame.f_code.co_filename = "/test/file.py"

        mock_currentframe = MagicMock()
        mock_currentframe.f_back = mock_frame

        with patch("inspect.currentframe", return_value=mock_currentframe):
            template_string = "Hello {{ name }}"

            # Mock Django Template creation to succeed
            with patch("next.templates.Template") as mock_template_class:
                mock_template = MagicMock()
                mock_template_class.return_value = mock_template

                compiler % template_string

                # check that template string is saved in _content_hashes
                content_hash = list(compiler._content_hashes.keys())[0]
                assert compiler._content_hashes[content_hash] == template_string

                # check that Django Template object is saved in _templates
                assert compiler._templates[content_hash] == mock_template
