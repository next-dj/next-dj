from next.testing import assert_has_class, assert_missing_class, find_anchor


class TestIndex:
    """The home page lists every post in alphabetical order."""

    def test_home_lists_both_posts(self, client) -> None:
        response = client.get("/")
        assert response.status_code == 200
        body = response.content.decode()
        assert "Latest posts" in body
        assert "Welcome to the blog" in body
        assert "Hello, world" in body
        assert "/posts/welcome/" in body
        assert "/posts/hello-world/" in body

    def test_home_shows_site_chrome_from_context_processor(self, client) -> None:
        response = client.get("/")
        body = response.content.decode()
        assert "Small posts, plain Markdown" in body
        assert "© " in body


class TestPost:
    """Each post renders through the nested posts layout."""

    def test_welcome_renders_body_and_meta(self, client) -> None:
        response = client.get("/posts/welcome/")
        assert response.status_code == 200
        body = response.content.decode()
        assert "Welcome to the blog" in body
        assert "<h2>Why Markdown?</h2>" in body
        assert "min read" in body
        assert "Back to posts" in body

    def test_hello_world_renders_fenced_code(self, client) -> None:
        response = client.get("/posts/hello-world/")
        assert response.status_code == 200
        body = response.content.decode()
        assert "Hello, world" in body
        assert 'class="language-python"' in body
        assert "print(" in body

    def test_reading_time_is_at_least_one_minute(self, client) -> None:
        response = client.get("/posts/welcome/")
        body = response.content.decode()
        assert "~ 1 min read" in body or "~ 2 min read" in body


class TestShareButton:
    """The share button sits inside the nested layout and reads window.Next.context.post."""

    def test_share_button_renders_on_post_page(self, client) -> None:
        response = client.get("/posts/welcome/")
        body = response.content.decode()
        assert "data-share" in body
        assert "Share" in body

    def test_share_button_not_on_home(self, client) -> None:
        response = client.get("/")
        assert "data-share" not in response.content.decode()

    def test_serialized_post_context_is_injected_for_js(self, client) -> None:
        response = client.get("/posts/welcome/")
        body = response.content.decode()
        assert '"title":"Welcome to the blog"' in body
        assert '"slug":"welcome"' in body


class TestActiveNav:
    """The shared nav_link component toggles active state via request.resolver_match."""

    def test_home_link_active_on_home(self, client) -> None:
        body = client.get("/").content.decode()
        assert_has_class(find_anchor(body, href="/", text="Home"), "font-semibold")
        assert_missing_class(
            find_anchor(body, href="/about/", text="About"),
            "font-semibold",
        )

    def test_about_link_active_on_about(self, client) -> None:
        body = client.get("/about/").content.decode()
        assert_has_class(
            find_anchor(body, href="/about/", text="About"), "font-semibold"
        )
        assert_missing_class(
            find_anchor(body, href="/", text="Home"),
            "font-semibold",
        )
