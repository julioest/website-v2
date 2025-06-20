import html
import json
import os
import re

import boto3
import structlog
from botocore.exceptions import ClientError
from bs4 import BeautifulSoup, Tag
from django.conf import settings
from mistletoe import HtmlRenderer
from mistletoe.span_token import SpanToken
from pygments import highlight
from pygments.formatter import Formatter
from pygments.lexers import get_lexer_by_name as get_lexer
from pygments.lexers import guess_lexer
from pygments.util import get_bool_opt

logger = structlog.get_logger()


def extract_file_data(response, s3_key):
    """Extracts the file content, content type, and last modified date from an S3
    response object."""
    file_content = response["Body"].read()
    content_type = get_content_type(s3_key, response["ContentType"])
    last_modified = response["LastModified"]
    return {
        "content": file_content,
        "content_key": s3_key,
        "content_type": content_type,
        "last_modified": last_modified,
    }


def get_meta_redirect_from_html(html_string: str) -> str | None:
    """Use BeautifulSoup to get the meta redirect from an HTML document, if it exists.

    Args:
        html_string (str): The HTML document as a string

    Returns:
        str: The redirect URL as a string, or None if no redirect exists.
    """
    soup = BeautifulSoup(html_string, "html.parser")
    meta_tag = soup.find("meta", attrs={"http-equiv": "refresh"})

    redirect_url = None
    if meta_tag:
        meta_tag_content = meta_tag.get("content")
        if meta_tag_content:
            if "URL=" in meta_tag_content:
                split_on = "URL="
            else:
                split_on = "url="
            redirect_url = meta_tag_content.split(split_on)[1]

    return redirect_url


def get_body_from_html(html_string: str) -> str:
    """Use BeautifulSoup to get the body content from an HTML document, without
    the <body> tag.

    We strip out the <body> tag because we want to use our main Boost template,
    which includes its own <body> tag. Skip any tag with an id containing 'footer'.

    Args:
        html_string (str): The HTML document as a string

    Returns:
        str: The body content as a string
    """
    soup = BeautifulSoup(html_string, "html.parser")
    body = soup.find("body")
    body_content = ""
    if body:
        body_content = "".join(
            str(tag)
            for tag in body.contents
            if isinstance(tag, Tag)
            and not (tag.get("id") and "footer" in tag.get("id"))
        )
    return body_content


def get_content_from_s3(key=None, bucket_name=None):
    """
    Get content from S3. Returns the decoded file contents if able
    """
    if not key:
        raise ValueError("No key provided.")

    bucket_name = bucket_name or settings.STATIC_CONTENT_BUCKET_NAME
    # s3_keys = get_s3_keys(key) or [key]
    # Force a successful lookup from get_s3_keys, otherwise no match at all.
    # That removes any random default "/" lookups.
    s3_keys = get_s3_keys(key) or []
    client = get_s3_client()

    for s3_key in s3_keys:
        file_data = get_file_data(client, bucket_name, s3_key)
        if file_data:
            return file_data

        # Handle URLs that are directories looking for `index.html` files
        if s3_key.endswith("/"):
            index_html_key = f"{s3_key}index.html"
            file_data = get_file_data(client, bucket_name, index_html_key)
            if file_data:
                return file_data

    logger.info(
        "get_content_from_s3_no_valid_object",
        key=key,
        bucket_name=bucket_name,
        function_name="get_content_from_s3",
    )
    return {}


def get_content_type(s3_key, content_type):
    """In some cases, manually set the content-type for a given S3 key based on the
    file extension. This is useful for files types that are not recognized by S3, or for
    cases where we want to override the default content-type.

    :param s3_key: The S3 key for the file
    :param content_type: The content-type returned by S3
    :return: The content-type for the file
    """
    if s3_key.endswith(".js"):
        content_type = "application/javascript"
    # adoc files come back from S3 with a generic content type, so we manually set
    # the content type to the (proposed) asciidoc content type:
    # https://docs.asciidoctor.org/asciidoc/latest/faq/
    elif s3_key.endswith(".adoc"):
        content_type = "text/asciidoc"
    return content_type


def get_file_data(client, bucket_name, s3_key):
    """Get the file data from S3. Returns the decoded file contents if able."""
    try:
        response = client.get_object(Bucket=bucket_name, Key=s3_key.lstrip("/"))
        return extract_file_data(response, s3_key)
    except ClientError as e:
        # Log the exception but ignore it otherwise, since it's not necessarily an error
        logger.exception(
            "get_content_from_s3_error",
            s3_key=s3_key,
            error=str(e),
            function_name="get_content_from_s3",
        )
    return


def get_s3_client():
    """Get an S3 client."""
    return boto3.client(
        "s3",
        aws_access_key_id=settings.STATIC_CONTENT_AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.STATIC_CONTENT_AWS_SECRET_ACCESS_KEY,
        region_name=settings.STATIC_CONTENT_REGION,
    )


def does_s3_key_exist(client, bucket_name, s3_key):
    try:
        client.head_object(Bucket=bucket_name, Key=s3_key.lstrip("/"))
        return True
    except ClientError:
        logger.debug(f"{s3_key} does not exist in {bucket_name}")
        return False


def get_s3_keys(content_path, config_filename=None):
    """
    Get the S3 key for a given content path
    """
    # Get configuration from settings if not specifically given
    if config_filename is None:
        config_filename = settings.STATIC_CONTENT_MAPPING

    # Get the config file for the static content URL settings.
    project_root = settings.BASE_DIR
    config_file_path = os.path.join(project_root, config_filename)

    if not content_path.startswith("/"):
        content_path = f"/{content_path}"

    with open(config_file_path, "r") as f:
        config_data = json.load(f)

    s3_keys = []
    for item in config_data:
        site_path = item["site_path"]
        s3_path = item["s3_path"]

        if site_path == "/" and content_path.startswith(site_path):
            if s3_path in content_path:
                s3_keys.append(content_path)
            else:
                s3_keys.append(os.path.join(s3_path, content_path.lstrip("/")))

        elif content_path.startswith(site_path):
            s3_keys.append(content_path.replace(site_path, s3_path))

    return s3_keys


def convert_img_paths(html_content: str, s3_path: str = None):
    """
    Convert all relative images paths to absolute paths.

    Args:
    - html_content: The HTML content you want to convert
    - s3_path: The key ultimately used to retrieve the HTML data.
        If present, will be whatever key from get_s3_keys() that worked
        to retrieve the HTML data

    Explanation:

    The config file allows us to add shortcut URLs to specific S3 keys. An example is
    the /help/ page; see the config file for how it maps the site_path to the S3 key
    that will retrieve the data.

    However, most images in these files will be relative, and when the config file
    masks the S3 keys, the image URLs can't be found in the browser.

    This function retrieves all images and updates their URLs to be fully-qualified
    by routing them through our `/images/` view, which will retrieve them from S3
    directly.

    NOTE: This hasn't been well-tested and it's possible it will need updates as
    we encounter more special cases related to the static content.
    """
    if not html_content:
        return

    if type(html_content) is not str:
        raise ValueError(
            f"HTML content must be a string, and it is {type(html_content)}."
        )

    soup = BeautifulSoup(html_content, "html.parser")

    for img in soup.find_all("img"):
        original_src = img.get("src", "")
        if not original_src.startswith(("http://", "https://")):
            # Construct the new absolute URL for the image
            new_src = "/".join([s3_path, original_src])
            if not new_src.startswith("/"):
                new_src = f"/{new_src}"
            img["src"] = new_src

    return str(soup)


class Youtube(SpanToken):
    """
    Span token for Youtube shortcodes
    Expected shortcode: `[[ youtube | U4VZ9DRdXAI ]]`
    youtube is thrown out but in the shortcode for readability
    """

    pattern = re.compile(r"\[\[ *(.+?) *\| *(.+?) *\]\]")

    def __init__(self, match):
        self.target = match.group(2)


class NoStyleHtmlFormatter(Formatter):
    """
    Custom formatter that avoids inline styles and classes.
    Outputs raw HTML without additional decorations.
    Useful in combination with highlight.js
    """

    def __init__(self, **options):
        super().__init__(**options)
        self.nowrap = get_bool_opt(options, "nowrap", False)

    def format(self, tokensource, outfile):
        for ttype, value in tokensource:
            # Escape HTML special characters to avoid issues
            outfile.write(html.escape(value))


class PygmentsRenderer(HtmlRenderer):
    formatter = NoStyleHtmlFormatter(nowrap=True)

    def render_block_code(self, token):
        code = token.children[0].content
        lexer = get_lexer(token.language) if token.language else guess_lexer(code)
        tokenized_code = highlight(code, lexer, self.formatter)
        return f'<pre class="highlightjs highlight"><code class="language-{token.language} hljs">{tokenized_code}</code></pre>'  # noqa E501


class BoostRenderer(PygmentsRenderer):
    def __init__(self):
        super().__init__(Youtube)

    def render_youtube(self, token):
        template = '<iframe width="560" height="315" src="https://www.youtube.com/embed/{target}" title="YouTube video player" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe>'  # noqa E501
        return template.format(target=token.target)
