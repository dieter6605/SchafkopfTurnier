from lxml import html


def form_client(app):
    """Routen mit handgebauten Testdaten über eine echte Formularsitzung aufrufen."""
    client = app.test_client()
    page = html.fromstring(client.get("/tournaments/new").get_data(as_text=True))
    token = page.get_element_by_id("tForm").fields["csrf_token"]
    client.environ_base["HTTP_X_CSRF_TOKEN"] = token
    return client
