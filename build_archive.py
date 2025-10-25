import os
import json
import requests
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

import argparse
import time

class Tic:
    def __init__(self):
        self.tic()

    def get_time(self):
        return time.perf_counter_ns()

    def process_diff(self, diff):
        return diff / 1e9

    def tic(self):
        self._last = self.get_time()
    
    def toc(self):
        diff = self.get_time() - self._last
        return self.process_diff(diff)


def make_parser(f=None):
    parser = argparse.ArgumentParser()
    if f:
        f(parser)
    subparsers = parser.add_subparsers()
    return parser, subparsers


class _EntryPoint:
    def __init__(self, f):
        self.f = f
        self._parser = None
        self.name = f.__name__

        f.parser = self.parser


    def prepare_parser(self, parser, subparsers):
        parser = subparsers.add_parser(self.name)
        if self._parser:
            self._parser(parser)

        parser.set_defaults(cmd=self.f)

    def parser(self, f):
        self._parser = f
        return f

class EntryPoints:
    def __init__(self):
        self.entrypoints = []
        self.parser_functions = []

    def common_parser(self, parser):
        for pf in self.parser_functions:
            pf(parser)

    def point(self, f):
        ep =  _EntryPoint(f)
        self.entrypoints.append(ep)
        return f

    def add_common_parser(self, f):
        self.parser_functions.append(f)
        return f
    

    def parse_args(self):
        parser, subparsers = make_parser(self.common_parser)
        for ep in self.entrypoints:
            ep.prepare_parser(parser, subparsers)

        args = parser.parse_args()
        return args

    def main(self):
        args = self.parse_args()
        tic = Tic()
        args.cmd(args)
        tdiff = tic.toc()
        print(f"Ran in {tdiff:0.05f} seconds")



def download_file(url, out_file):
    content = requests.get(url, stream=True).content
    with open(out_file, "wb") as f:
        f.write(content)

def load_json(file="conf.json"):
    with open(file, encoding='utf8') as f:
        return json.load(f)


class HTML2MarkdownParser(HTMLParser):
    """Convert HTML to Markdown with wiki-link support for internal links"""
    
    def __init__(self, base_url, post_id_map):
        super().__init__()
        self.base_url = base_url
        self.post_id_map = post_id_map  # Maps URLs to post IDs
        self.markdown = []
        self.tag_stack = []
        self.list_depth = 0
        self.in_pre = False
        self.in_code = False
        
    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        
        if tag == 'p':
            self.markdown.append('\n\n')
        elif tag == 'br':
            self.markdown.append('  \n')
        elif tag == 'strong' or tag == 'b':
            self.markdown.append('**')
            self.tag_stack.append('**')
        elif tag == 'em' or tag == 'i':
            self.markdown.append('*')
            self.tag_stack.append('*')
        elif tag == 'code':
            self.markdown.append('`')
            self.tag_stack.append('`')
            self.in_code = True
        elif tag == 'pre':
            self.markdown.append('\n```\n')
            self.in_pre = True
        elif tag == 'a':
            href = attrs_dict.get('href', '')
            self.tag_stack.append(('link', href))
        elif tag in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            level = int(tag[1])
            self.markdown.append('\n' + '#' * level + ' ')
            self.tag_stack.append('header')
        elif tag == 'ul' or tag == 'ol':
            self.list_depth += 1
            self.tag_stack.append(('list', tag))
        elif tag == 'li':
            indent = '  ' * (self.list_depth - 1)
            list_marker = '- ' if self.tag_stack and self.tag_stack[-1][1] == 'ul' else '1. '
            self.markdown.append(f'\n{indent}{list_marker}')
        elif tag == 'blockquote':
            self.markdown.append('\n> ')
            self.tag_stack.append('blockquote')
            
    def handle_endtag(self, tag):
        if tag == 'p':
            pass  # Already handled in starttag
        elif tag in ['strong', 'b', 'em', 'i']:
            if self.tag_stack and self.tag_stack[-1] in ['**', '*']:
                self.markdown.append(self.tag_stack.pop())
        elif tag == 'code':
            if self.tag_stack and self.tag_stack[-1] == '`':
                self.markdown.append(self.tag_stack.pop())
            self.in_code = False
        elif tag == 'pre':
            self.markdown.append('\n```\n')
            self.in_pre = False
        elif tag == 'a':
            if self.tag_stack and isinstance(self.tag_stack[-1], tuple) and self.tag_stack[-1][0] == 'link':
                _, href = self.tag_stack.pop()
                link_text = self.markdown.pop() if self.markdown else ''
                
                # Skip empty or None hrefs
                if not href:
                    self.markdown.append(link_text)
                else:
                    try:
                        # Check if it's an internal link
                        if self.is_internal_link(href):
                            # Convert to wiki link
                            target_id = self.get_post_id_from_url(href)
                            if target_id:
                                self.markdown.append(f'[[{target_id}|{link_text}]]')
                            else:
                                # Fallback to regular link if we can't find the post
                                self.markdown.append(f'[{link_text}]({href})')
                        else:
                            # External link - keep as is
                            self.markdown.append(f'[{link_text}]({href})')
                    except (ValueError, Exception):
                        # If URL parsing fails, just output as plain text with the href
                        self.markdown.append(f'{link_text} ({href})')
        elif tag in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            if self.tag_stack and self.tag_stack[-1] == 'header':
                self.tag_stack.pop()
            self.markdown.append('\n')
        elif tag == 'ul' or tag == 'ol':
            self.list_depth -= 1
            if self.tag_stack:
                self.tag_stack.pop()
        elif tag == 'blockquote':
            if self.tag_stack and self.tag_stack[-1] == 'blockquote':
                self.tag_stack.pop()
                
    def handle_data(self, data):
        if self.in_pre or self.in_code:
            self.markdown.append(data)
        else:
            # For link text, store temporarily
            if self.tag_stack and isinstance(self.tag_stack[-1], tuple) and self.tag_stack[-1][0] == 'link':
                self.markdown.append(data)
            else:
                self.markdown.append(data)
    
    def is_internal_link(self, url):
        """Check if URL is internal to the blog"""
        parsed = urlparse(url)
        base_parsed = urlparse(self.base_url)
        return parsed.netloc == base_parsed.netloc or not parsed.netloc
    
    def get_post_id_from_url(self, url):
        """Get post ID from URL using the post_id_map"""
        # Normalize URL
        url = url.strip()
        return self.post_id_map.get(url)
    
    def get_markdown(self):
        """Return the final markdown"""
        result = ''.join(self.markdown)
        # Clean up multiple newlines
        result = re.sub(r'\n{3,}', '\n\n', result)
        return result.strip()


class ObsidianVaultBuilder:
    """Builds an Obsidian vault from blog and forum data"""
    
    def __init__(self, conf):
        self.conf = conf
        self.root = conf['root']
        self.vault_path = os.path.join(self.root, conf.get('vault_path', 'vault'))
        self.blog_path = os.path.join(self.vault_path, 'Blog')
        self.fvp_forum_path = os.path.join(self.vault_path, 'FVP Forum')
        self.general_forum_path = os.path.join(self.vault_path, 'General Forum')
        os.makedirs(self.blog_path, exist_ok=True)
        os.makedirs(self.fvp_forum_path, exist_ok=True)
        os.makedirs(self.general_forum_path, exist_ok=True)
    
    def sanitize_filename(self, title):
        """Create a safe filename from a title"""
        # Replace non-breaking spaces with regular spaces
        safe = title.replace('\xa0', ' ')
        # Remove or replace invalid characters
        safe = re.sub(r'[<>:"/\\|?*#^]', '', safe)
        safe = safe.strip()
        # Limit length
        if len(safe) > 200:
            safe = safe[:200]
        return safe
    
    def build_post_id_map(self, posts, subfolder=None):
        """Build a mapping of URLs to post filenames (without .md extension)"""
        post_map = {}
        for post in posts:
            # Map URL to the sanitized filename (with subfolder if provided)
            filename = self.sanitize_filename(post['title'])
            if subfolder:
                filename = f"{subfolder}/{filename}"
            post_map[post['url']] = filename
        return post_map
    
    def build_topic_id_map(self, topics, subfolder=None):
        """Build a mapping of URLs to topic filenames (without .md extension)"""
        topic_map = {}
        for topic in topics:
            # Map URL to the sanitized filename (with subfolder if provided)
            filename = self.sanitize_filename(topic['title'])
            if subfolder:
                filename = f"{subfolder}/{filename}"
            topic_map[topic['url']] = filename
        return topic_map
    
    def build_unified_id_map(self, blog_data, fvp_forum_data, general_forum_data):
        """Build a unified mapping of all URLs across blog and forums"""
        unified_map = {}
        
        # Add blog posts
        unified_map.update(self.build_post_id_map(blog_data['posts'], 'Blog'))
        
        # Add FVP forum topics
        unified_map.update(self.build_topic_id_map(fvp_forum_data['topics'], 'FVP Forum'))
        
        # Add General forum topics
        unified_map.update(self.build_topic_id_map(general_forum_data['topics'], 'General Forum'))
        
        return unified_map
    
    def html_to_markdown(self, html, base_url, post_id_map):
        """Convert HTML to Markdown"""
        parser = HTML2MarkdownParser(base_url, post_id_map)
        parser.feed(html)
        return parser.get_markdown()
    
    def format_date(self, date_obj):
        """Format date object to readable string"""
        return f"{date_obj['year']}-{date_obj['month']}-{date_obj['day']} {date_obj.get('time', '00:00')}"

    def create_blog_index(self, posts):
        """Create an index file listing all blog posts in reverse chronological order"""
        md = []
        
        md.append('# Blog Archive')
        md.append('')
        md.append(f'Total posts: {len(posts)}')
        md.append('')
        
        # Sort posts by date (reverse chronological)
        sorted_posts = sorted(posts, key=lambda p: (
            int(p['date']['year']),
            int(p['date']['month']),
            int(p['date']['day']),
            p['date'].get('time', '00:00')
        ), reverse=True)
        
        for post in sorted_posts:
            filename = self.sanitize_filename(post['title'])
            date_str = self.format_date(post['date'])
            
            # Create entry with wiki link
            md.append(f"- [[Blog/{filename}|{post['title']}]] - *{date_str}*")
            
            # Add tags if present
            if post.get('tags'):
                md.append(f"  - Tags: {', '.join(['#'+self.sanitize_tag(t) for t in post['tags']])}")
        
        # Write index file
        index_path = os.path.join(self.vault_path, 'Blog Archive.md')
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(md))
        
        print(f"Created blog archive index at {index_path}")
    
    def sanitize_tag(self, tag):
        safe = re.sub(r'[ ]', '', tag)
        return safe

    def build_blog_post(self, post, post_id_map, base_url):
        """Convert a single blog post to markdown"""
        md = []
        
        # Frontmatter
        md.append('---')
        md.append(f"id: {post['id']}")
        md.append(f"title: \"{post['title']}\"")
        md.append(f"date: {self.format_date(post['date'])}")
        md.append(f"url: {post['url']}")
        if post.get('tags'):
            md.append(f"tags: [{', '.join([self.sanitize_tag(t) for t in post['tags']])}]")
        md.append('---')
        md.append('')
        
        # Title
        md.append(f"# {post['title']}")
        md.append('')
        
        # Date
        md.append(f"*Posted: {self.format_date(post['date'])}*")
        md.append('')
        
        # Body
        body_md = self.html_to_markdown(post['body'], base_url, post_id_map)
        md.append(body_md)
        md.append('')
        
        # Comments
        if post.get('comments'):
            md.append('---')
            md.append('')
            md.append(f"## Comments ({len(post['comments'])})")
            md.append('')
            
            for comment in post['comments']:
                md.append(f"### {comment['author']} - {self.format_date(comment['date'])}")
                md.append('')
                md.append(self.html_to_markdown(comment['body'], base_url, post_id_map))
                md.append('')
        
        return '\n'.join(md)
    
    def build_blog_vault(self, blog_data, unified_id_map):
        """Build vault from blog posts"""
        posts = blog_data['posts']
        base_url = posts[0]['url'] if posts else 'http://markforster.squarespace.com'
        
        for post in posts:
            # Create filename from title
            filename = self.sanitize_filename(post['title']) + '.md'
            filepath = os.path.join(self.blog_path, filename)
            
            # Generate markdown
            markdown = self.build_blog_post(post, unified_id_map, base_url)
            
            # Write file
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(markdown)
        
        # Create blog archive index
        self.create_blog_index(posts)
        print(f"Created {len(posts)} blog post files in {self.blog_path}")
    
    def get_latest_post_date(self, topic):
        """Get the date of the most recent post in a topic"""
        if not topic.get('posts'):
            # Fallback to topic creation date if no posts
            return (
                int(topic['date']['year']),
                int(topic['date']['month']),
                int(topic['date']['day']),
                topic['date'].get('time', '00:00')
            )
        
        # Get the last post's date (posts should be in chronological order)
        last_post = topic['posts'][-1]
        return (
            int(last_post['date']['year']),
            int(last_post['date']['month']),
            int(last_post['date']['day']),
            last_post['date'].get('time', '00:00')
        )
    
    def build_forum_topic(self, topic, topic_id_map, base_url):
        """Convert a single forum topic to markdown"""
        md = []
        
        # Frontmatter
        md.append('---')
        md.append(f"id: {topic['id']}")
        md.append(f"title: \"{topic['title']}\"")
        md.append(f"date: {self.format_date(topic['date'])}")
        md.append(f"author: {topic['author']}")
        md.append(f"url: {topic['url']}")
        if topic.get('tags'):
            md.append(f"tags: [{', '.join([self.sanitize_tag(t) for t in topic['tags']])}]")
        md.append('---')
        md.append('')
        
        # Title
        md.append(f"# {topic['title']}")
        md.append('')
        
        # Topic info
        md.append(f"**Author:** {topic['author']}")
        md.append(f"**Created:** {self.format_date(topic['date'])}")
        if topic.get('posts'):
            last_post_date = self.format_date(topic['posts'][-1]['date'])
            md.append(f"**Last Activity:** {last_post_date}")
        md.append('')
        md.append('---')
        md.append('')
        
        # Posts
        if topic.get('posts'):
            for i, post in enumerate(topic['posts']):
                # First post is the topic body
                if i == 0:
                    md.append('## Original Post')
                    md.append('')
                else:
                    md.append(f"## Reply by {post['author']}")
                    md.append('')
                
                md.append(f"*{self.format_date(post['date'])}*")
                md.append('')
                
                # Convert body to markdown
                body_md = self.html_to_markdown(post['body'], base_url, topic_id_map)
                md.append(body_md)
                md.append('')
                md.append('---')
                md.append('')
        
        return '\n'.join(md)
    
    def create_forum_index(self, topics, forum_name, output_path):
        """Create an index file listing all forum topics sorted by last activity"""
        md = []
        
        md.append(f'# {forum_name} Archive')
        md.append('')
        md.append(f'Total topics: {len(topics)}')
        md.append('')
        
        # Sort topics by most recent post date (reverse chronological)
        sorted_topics = sorted(topics, key=self.get_latest_post_date, reverse=True)
        
        for topic in sorted_topics:
            filename = self.sanitize_filename(topic['title'])
            created_date = self.format_date(topic['date'])
            latest_date = self.format_date(topic['posts'][-1]['date']) if topic.get('posts') else created_date
            
            # Create entry with wiki link
            md.append(f"- [[{forum_name}/{filename}|{topic['title']}]]")
            md.append(f"  - Created: *{created_date}* by {topic['author']}")
            md.append(f"  - Last Activity: *{latest_date}*")
            if topic.get('posts'):
                md.append(f"  - Replies: {len(topic['posts']) - 1}")
            
            # Add tags if present
            if topic.get('tags'):
                md.append(f"  - Tags: {', '.join(['#'+self.sanitize_tag(t) for t in topic['tags']])}")
        
        # Write index file
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(md))
        
        print(f"Created {forum_name} index at {output_path}")
    
    def build_forum_vault(self, forum_data, forum_name, forum_path, base_url, unified_id_map):
        """Build vault from forum topics"""
        topics = forum_data['topics']
        
        for topic in topics:
            # Create filename from title
            filename = self.sanitize_filename(topic['title']) + '.md'
            filepath = os.path.join(forum_path, filename)
            
            # Generate markdown
            markdown = self.build_forum_topic(topic, unified_id_map, base_url)
            
            # Write file
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(markdown)
        
        # Create forum index
        index_path = os.path.join(self.vault_path, f'{forum_name} Archive.md')
        self.create_forum_index(topics, forum_name, index_path)
        
        print(f"Created {len(topics)} forum topic files in {forum_path}")


class HTMLSiteBuilder:
    """Builds a standalone HTML site from blog and forum data"""
    
    def __init__(self, conf):
        self.conf = conf
        self.root = conf['root']
        self.html_path = os.path.join(self.root, conf.get('html_path', 'html_site'))
        self.blog_path = os.path.join(self.html_path, 'blog')
        self.fvp_forum_path = os.path.join(self.html_path, 'fvp_forum')
        self.general_forum_path = os.path.join(self.html_path, 'general_forum')
        os.makedirs(self.blog_path, exist_ok=True)
        os.makedirs(self.fvp_forum_path, exist_ok=True)
        os.makedirs(self.general_forum_path, exist_ok=True)
        
        # Create default CSS file
        self.create_default_css()
    
    def create_default_css(self):
        """Create a simple brutalist CSS file"""
        css = """
/* Brutalist Design - Simple and Functional */
* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: sans-serif;
    line-height: 1.6;
    max-width: 900px;
    margin: 0 auto;
    padding: 20px;
    background: #fff;
    color: #000;
}

header {
    border-bottom: 2px solid #000;
    margin-bottom: 40px;
    padding-bottom: 20px;
}

h1 {
    font-size: 2em;
    margin-bottom: 10px;
    font-weight: bold;
}

h2 {
    font-size: 1.5em;
    margin-top: 30px;
    margin-bottom: 15px;
    font-weight: bold;
}

h3 {
    font-size: 1.2em;
    margin-top: 20px;
    margin-bottom: 10px;
    font-weight: bold;
}

a {
    color: #000;
    text-decoration: underline;
}

a:hover {
    background: #000;
    color: #fff;
}

nav {
    margin-bottom: 20px;
    padding: 10px 0;
}

nav a {
    margin-right: 15px;
    text-decoration: none;
    font-weight: bold;
}

.meta {
    color: #666;
    font-size: 0.9em;
    margin-bottom: 20px;
}

.content {
    margin-bottom: 40px;
}

.comment {
    border-left: 3px solid #000;
    padding-left: 20px;
    margin: 30px 0;
}

.comment-meta {
    font-weight: bold;
    margin-bottom: 10px;
}

ul, ol {
    margin-left: 20px;
    margin-bottom: 20px;
}

li {
    margin-bottom: 10px;
}

blockquote {
    border-left: 3px solid #000;
    padding-left: 20px;
    margin: 20px 0;
    font-style: italic;
}

code {
    background: #f0f0f0;
    padding: 2px 5px;
    border: 1px solid #000;
    font-family: monospace;
}

pre {
    background: #f0f0f0;
    padding: 15px;
    border: 2px solid #000;
    overflow-x: auto;
    margin: 20px 0;
    font-family: monospace;
}

hr {
    border: none;
    border-top: 1px solid #ccc;
    margin: 40px 0;
}

.index-item {
    margin-bottom: 30px;
    padding-bottom: 20px;
}

.index-item:not(:last-child) {
    border-bottom: 1px solid #eee;
}

footer {
    margin-top: 60px;
    padding-top: 20px;
    border-top: 2px solid #000;
    font-size: 0.9em;
}
"""
        css_path = os.path.join(self.html_path, 'style.css')
        with open(css_path, 'w', encoding='utf-8') as f:
            f.write(css.strip())
    
    def sanitize_filename(self, title):
        """Create a safe filename from a title"""
        safe = title.replace('\xa0', ' ')
        safe = re.sub(r'[<>:"/\\|?*#^]', '', safe)
        safe = re.sub(r'\s+', '_', safe)
        safe = safe.strip('_')
        if len(safe) > 200:
            safe = safe[:200]
        return safe.lower()
    
    def format_date(self, date_obj):
        """Format date object to readable string"""
        return f"{date_obj['year']}-{date_obj['month']}-{date_obj['day']} {date_obj.get('time', '00:00')}"
    
    def build_unified_url_map(self, blog_data, fvp_forum_data, general_forum_data):
        """Build a unified mapping of URLs to HTML file paths"""
        url_map = {}
        
        # Blog posts
        for post in blog_data['posts']:
            filename = self.sanitize_filename(post['title']) + '.html'
            url_map[post['url']] = f'../blog/{filename}'
        
        # FVP Forum topics
        for topic in fvp_forum_data['topics']:
            filename = self.sanitize_filename(topic['title']) + '.html'
            url_map[topic['url']] = f'../fvp_forum/{filename}'
        
        # General Forum topics
        for topic in general_forum_data['topics']:
            filename = self.sanitize_filename(topic['title']) + '.html'
            url_map[topic['url']] = f'../general_forum/{filename}'
        
        return url_map
    
    def convert_links_to_html(self, html, base_url, url_map):
        """Convert internal links in HTML to local file links"""
        from html.parser import HTMLParser
        
        class LinkConverter(HTMLParser):
            def __init__(self, base_url, url_map):
                super().__init__()
                self.base_url = base_url
                self.url_map = url_map
                self.output = []
            
            def handle_starttag(self, tag, attrs):
                if tag == 'a':
                    new_attrs = []
                    href = None
                    for attr, value in attrs:
                        if attr == 'href':
                            href = value
                        else:
                            new_attrs.append(f'{attr}="{value}"')
                    
                    if href:
                        # Check if internal link
                        try:
                            parsed = urlparse(href)
                            base_parsed = urlparse(base_url)
                            is_internal = parsed.netloc == base_parsed.netloc or not parsed.netloc
                            
                            if is_internal and href in url_map:
                                href = url_map[href]
                        except:
                            pass
                        
                        new_attrs.insert(0, f'href="{href}"')
                    
                    attrs_str = ' '.join(new_attrs)
                    self.output.append(f'<{tag} {attrs_str}>')
                else:
                    attrs_str = ' '.join([f'{k}="{v}"' for k, v in attrs])
                    if attrs_str:
                        self.output.append(f'<{tag} {attrs_str}>')
                    else:
                        self.output.append(f'<{tag}>')
            
            def handle_endtag(self, tag):
                self.output.append(f'</{tag}>')
            
            def handle_data(self, data):
                self.output.append(data)
            
            def get_html(self):
                return ''.join(self.output)
        
        converter = LinkConverter(base_url, url_map)
        try:
            converter.feed(html)
            return converter.get_html()
        except:
            return html
    
    def build_html_template(self, title, content, nav_prefix=''):
        """Build HTML page with template
        
        nav_prefix: prefix for navigation links (empty string for root level, '../' for subdirs)
        """
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <link rel="stylesheet" href="{nav_prefix}style.css">
</head>
<body>
    <header>
        <nav>
            <a href="{nav_prefix}index.html">Home</a>
            <a href="{nav_prefix}blog_index.html">Blog</a>
            <a href="{nav_prefix}fvp_forum_index.html">FVP Forum</a>
            <a href="{nav_prefix}general_forum_index.html">General Forum</a>
        </nav>
    </header>
    <main>
{content}
    </main>
    <footer>
        <p>Archive built from Mark Forster's blog and forums</p>
    </footer>
</body>
</html>"""
    
    def build_blog_post_html(self, post, url_map, base_url):
        """Convert a blog post to HTML"""
        content = []
        
        content.append(f'<article>')
        content.append(f'<h1>{post["title"]}</h1>')
        content.append(f'<div class="meta">Posted: {self.format_date(post["date"])}</div>')
        content.append(f'<div class="content">{self.convert_links_to_html(post["body"], base_url, url_map)}</div>')
        
        # Comments
        if post.get('comments'):
            content.append(f'<hr>')
            content.append(f'<h2>Comments ({len(post["comments"])})</h2>')
            for comment in post['comments']:
                content.append(f'<div class="comment">')
                content.append(f'<div class="comment-meta">{comment["author"]} - {self.format_date(comment["date"])}</div>')
                content.append(f'<div>{self.convert_links_to_html(comment["body"], base_url, url_map)}</div>')
                content.append(f'</div>')
        
        content.append(f'</article>')
        return '\n'.join(content)
    
    def build_forum_topic_html(self, topic, url_map, base_url):
        """Convert a forum topic to HTML"""
        content = []
        
        content.append(f'<article>')
        content.append(f'<h1>{topic["title"]}</h1>')
        content.append(f'<div class="meta">')
        content.append(f'Author: {topic["author"]} | ')
        content.append(f'Created: {self.format_date(topic["date"])}')
        if topic.get('posts'):
            content.append(f' | Last Activity: {self.format_date(topic["posts"][-1]["date"])}')
        content.append(f'</div>')
        
        # Posts
        if topic.get('posts'):
            for i, post in enumerate(topic['posts']):
                if i == 0:
                    content.append(f'<h2>Original Post</h2>')
                else:
                    content.append(f'<h2>Reply by {post["author"]}</h2>')
                
                content.append(f'<div class="meta">{self.format_date(post["date"])}</div>')
                content.append(f'<div class="content">{self.convert_links_to_html(post["body"], base_url, url_map)}</div>')
        
        content.append(f'</article>')
        return '\n'.join(content)
    
    def build_blog_html(self, blog_data, url_map):
        """Build HTML files for all blog posts"""
        posts = blog_data['posts']
        base_url = posts[0]['url'] if posts else 'http://markforster.squarespace.com'
        
        for post in posts:
            filename = self.sanitize_filename(post['title']) + '.html'
            filepath = os.path.join(self.blog_path, filename)
            
            content = self.build_blog_post_html(post, url_map, base_url)
            html = self.build_html_template(post['title'], content, nav_prefix='../')
            
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(html)
        
        print(f"Created {len(posts)} blog HTML files in {self.blog_path}")
    
    def build_forum_html(self, forum_data, forum_dir, forum_name, base_url, url_map):
        """Build HTML files for all forum topics"""
        topics = forum_data['topics']
        forum_path = os.path.join(self.html_path, forum_dir)
        
        for topic in topics:
            filename = self.sanitize_filename(topic['title']) + '.html'
            filepath = os.path.join(forum_path, filename)
            
            content = self.build_forum_topic_html(topic, url_map, base_url)
            html = self.build_html_template(topic['title'], content, nav_prefix='../')
            
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(html)
        
        print(f"Created {len(topics)} {forum_name} HTML files in {forum_path}")
    
    def build_blog_index_html(self, blog_data):
        """Build index page for blog"""
        posts = blog_data['posts']
        sorted_posts = sorted(posts, key=lambda p: (
            int(p['date']['year']),
            int(p['date']['month']),
            int(p['date']['day']),
            p['date'].get('time', '00:00')
        ), reverse=True)
        
        content = []
        content.append('<h1>Blog Archive</h1>')
        content.append(f'<p>Total posts: {len(posts)}</p>')
        
        for post in sorted_posts:
            filename = self.sanitize_filename(post['title']) + '.html'
            content.append(f'<div class="index-item">')
            content.append(f'<h2><a href="blog/{filename}">{post["title"]}</a></h2>')
            content.append(f'<div class="meta">{self.format_date(post["date"])}</div>')
            content.append(f'</div>')
        
        html = self.build_html_template('Blog Archive', '\n'.join(content), nav_prefix='')
        
        index_path = os.path.join(self.html_path, 'blog_index.html')
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write(html)
    
    def get_latest_post_date(self, topic):
        """Get the date of the most recent post in a topic"""
        if not topic.get('posts'):
            return (
                int(topic['date']['year']),
                int(topic['date']['month']),
                int(topic['date']['day']),
                topic['date'].get('time', '00:00')
            )
        
        last_post = topic['posts'][-1]
        return (
            int(last_post['date']['year']),
            int(last_post['date']['month']),
            int(last_post['date']['day']),
            last_post['date'].get('time', '00:00')
        )
    
    def build_forum_index_html(self, forum_data, forum_dir, forum_name):
        """Build index page for a forum"""
        topics = forum_data['topics']
        sorted_topics = sorted(topics, key=self.get_latest_post_date, reverse=True)
        
        content = []
        content.append(f'<h1>{forum_name} Archive</h1>')
        content.append(f'<p>Total topics: {len(topics)}</p>')
        
        for topic in sorted_topics:
            filename = self.sanitize_filename(topic['title']) + '.html'
            created_date = self.format_date(topic['date'])
            latest_date = self.format_date(topic['posts'][-1]['date']) if topic.get('posts') else created_date
            
            content.append(f'<div class="index-item">')
            content.append(f'<h2><a href="{forum_dir}/{filename}">{topic["title"]}</a></h2>')
            content.append(f'<div class="meta">')
            content.append(f'Created: {created_date} by {topic["author"]} | ')
            content.append(f'Last Activity: {latest_date}')
            if topic.get('posts'):
                content.append(f' | Replies: {len(topic["posts"]) - 1}')
            content.append(f'</div>')
            content.append(f'</div>')
        
        html = self.build_html_template(f'{forum_name} Archive', '\n'.join(content), nav_prefix='')
        
        index_path = os.path.join(self.html_path, f'{forum_dir}_index.html')
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write(html)
    
    def build_main_index_html(self, blog_data, fvp_forum_data, general_forum_data):
        """Build main index page"""
        content = []
        content.append('<h1>Mark Forster Archive</h1>')
        content.append('<p>Archive of Mark Forster\'s blog and forum discussions on time management and productivity.</p>')
        
        content.append('<h2>Collections</h2>')
        content.append(f'<ul>')
        content.append(f'<li><a href="blog_index.html">Blog</a> - {len(blog_data["posts"])} posts</li>')
        content.append(f'<li><a href="fvp_forum_index.html">FVP Forum</a> - {len(fvp_forum_data["topics"])} topics</li>')
        content.append(f'<li><a href="general_forum_index.html">General Forum</a> - {len(general_forum_data["topics"])} topics</li>')
        content.append(f'</ul>')
        
        html = self.build_html_template('Mark Forster Archive', '\n'.join(content), nav_prefix='')
        
        index_path = os.path.join(self.html_path, 'index.html')
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write(html)
        
    def sanitize_filename(self, title):
        """Create a safe filename from a title"""
        # Replace non-breaking spaces with regular spaces
        safe = title.replace('\xa0', ' ')
        # Remove or replace invalid characters
        safe = re.sub(r'[<>:"/\\|?*#^]', '', safe)
        safe = safe.strip()
        # Limit length
        if len(safe) > 200:
            safe = safe[:200]
        return safe
    
    def build_post_id_map(self, posts, subfolder=None):
        """Build a mapping of URLs to post filenames (without .md extension)"""
        post_map = {}
        for post in posts:
            # Map URL to the sanitized filename (with subfolder if provided)
            filename = self.sanitize_filename(post['title'])
            if subfolder:
                filename = f"{subfolder}/{filename}"
            post_map[post['url']] = filename
        return post_map
    
    def build_topic_id_map(self, topics, subfolder=None):
        """Build a mapping of URLs to topic filenames (without .md extension)"""
        topic_map = {}
        for topic in topics:
            # Map URL to the sanitized filename (with subfolder if provided)
            filename = self.sanitize_filename(topic['title'])
            if subfolder:
                filename = f"{subfolder}/{filename}"
            topic_map[topic['url']] = filename
        return topic_map
    
    def build_unified_id_map(self, blog_data, fvp_forum_data, general_forum_data):
        """Build a unified mapping of all URLs across blog and forums"""
        unified_map = {}
        
        # Add blog posts
        unified_map.update(self.build_post_id_map(blog_data['posts'], 'Blog'))
        
        # Add FVP forum topics
        unified_map.update(self.build_topic_id_map(fvp_forum_data['topics'], 'FVP Forum'))
        
        # Add General forum topics
        unified_map.update(self.build_topic_id_map(general_forum_data['topics'], 'General Forum'))
        
        return unified_map
    
    def html_to_markdown(self, html, base_url, post_id_map):
        """Convert HTML to Markdown"""
        parser = HTML2MarkdownParser(base_url, post_id_map)
        parser.feed(html)
        return parser.get_markdown()
    
    def format_date(self, date_obj):
        """Format date object to readable string"""
        return f"{date_obj['year']}-{date_obj['month']}-{date_obj['day']} {date_obj.get('time', '00:00')}"

    def create_blog_index(self, posts):
        """Create an index file listing all blog posts in reverse chronological order"""
        md = []
        
        md.append('# Blog Archive')
        md.append('')
        md.append(f'Total posts: {len(posts)}')
        md.append('')
        
        # Sort posts by date (reverse chronological)
        sorted_posts = sorted(posts, key=lambda p: (
            int(p['date']['year']),
            int(p['date']['month']),
            int(p['date']['day']),
            p['date'].get('time', '00:00')
        ), reverse=True)
        
        for post in sorted_posts:
            filename = self.sanitize_filename(post['title'])
            date_str = self.format_date(post['date'])
            
            # Create entry with wiki link
            md.append(f"- [[Blog/{filename}|{post['title']}]] - *{date_str}*")
            
            # Add tags if present
            if post.get('tags'):
                md.append(f"  - Tags: {', '.join(['#'+self.sanitize_tag(t) for t in post['tags']])}")
        
        # Write index file
        index_path = os.path.join(self.vault_path, 'Blog Archive.md')
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(md))
        
        print(f"Created blog archive index at {index_path}")
    
    def sanitize_tag(self, tag):
        safe = re.sub(r'[ ]', '', tag)
        return safe

    def build_blog_post(self, post, post_id_map, base_url):
        """Convert a single blog post to markdown"""
        md = []
        
        # Frontmatter
        md.append('---')
        md.append(f"id: {post['id']}")
        md.append(f"title: \"{post['title']}\"")
        md.append(f"date: {self.format_date(post['date'])}")
        md.append(f"url: {post['url']}")
        if post.get('tags'):
            md.append(f"tags: [{', '.join([self.sanitize_tag(t) for t in post['tags']])}]")
        md.append('---')
        md.append('')
        
        # Title
        md.append(f"# {post['title']}")
        md.append('')
        
        # Date
        md.append(f"*Posted: {self.format_date(post['date'])}*")
        md.append('')
        
        # Body
        body_md = self.html_to_markdown(post['body'], base_url, post_id_map)
        md.append(body_md)
        md.append('')
        
        # Comments
        if post.get('comments'):
            md.append('---')
            md.append('')
            md.append(f"## Comments ({len(post['comments'])})")
            md.append('')
            
            for comment in post['comments']:
                md.append(f"### {comment['author']} - {self.format_date(comment['date'])}")
                md.append('')
                md.append(self.html_to_markdown(comment['body'], base_url, post_id_map))
                md.append('')
        
        return '\n'.join(md)
    
    def build_blog_vault(self, blog_data, unified_id_map):
        """Build vault from blog posts"""
        posts = blog_data['posts']
        base_url = posts[0]['url'] if posts else 'http://markforster.squarespace.com'
        
        for post in posts:
            # Create filename from title
            filename = self.sanitize_filename(post['title']) + '.md'
            filepath = os.path.join(self.blog_path, filename)
            
            # Generate markdown
            markdown = self.build_blog_post(post, unified_id_map, base_url)
            
            # Write file
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(markdown)
        
        # Create blog archive index
        self.create_blog_index(posts)
        print(f"Created {len(posts)} blog post files in {self.blog_path}")
    
    def get_latest_post_date(self, topic):
        """Get the date of the most recent post in a topic"""
        if not topic.get('posts'):
            # Fallback to topic creation date if no posts
            return (
                int(topic['date']['year']),
                int(topic['date']['month']),
                int(topic['date']['day']),
                topic['date'].get('time', '00:00')
            )
        
        # Get the last post's date (posts should be in chronological order)
        last_post = topic['posts'][-1]
        return (
            int(last_post['date']['year']),
            int(last_post['date']['month']),
            int(last_post['date']['day']),
            last_post['date'].get('time', '00:00')
        )
    
    def build_forum_topic(self, topic, topic_id_map, base_url):
        """Convert a single forum topic to markdown"""
        md = []
        
        # Frontmatter
        md.append('---')
        md.append(f"id: {topic['id']}")
        md.append(f"title: \"{topic['title']}\"")
        md.append(f"date: {self.format_date(topic['date'])}")
        md.append(f"author: {topic['author']}")
        md.append(f"url: {topic['url']}")
        if topic.get('tags'):
            md.append(f"tags: [{', '.join([self.sanitize_tag(t) for t in topic['tags']])}]")
        md.append('---')
        md.append('')
        
        # Title
        md.append(f"# {topic['title']}")
        md.append('')
        
        # Topic info
        md.append(f"**Author:** {topic['author']}")
        md.append(f"**Created:** {self.format_date(topic['date'])}")
        if topic.get('posts'):
            last_post_date = self.format_date(topic['posts'][-1]['date'])
            md.append(f"**Last Activity:** {last_post_date}")
        md.append('')
        md.append('---')
        md.append('')
        
        # Posts
        if topic.get('posts'):
            for i, post in enumerate(topic['posts']):
                # First post is the topic body
                if i == 0:
                    md.append('## Original Post')
                    md.append('')
                else:
                    md.append(f"## Reply by {post['author']}")
                    md.append('')
                
                md.append(f"*{self.format_date(post['date'])}*")
                md.append('')
                
                # Convert body to markdown
                body_md = self.html_to_markdown(post['body'], base_url, topic_id_map)
                md.append(body_md)
                md.append('')
                md.append('---')
                md.append('')
        
        return '\n'.join(md)
    
    def create_forum_index(self, topics, forum_name, output_path):
        """Create an index file listing all forum topics sorted by last activity"""
        md = []
        
        md.append(f'# {forum_name} Archive')
        md.append('')
        md.append(f'Total topics: {len(topics)}')
        md.append('')
        
        # Sort topics by most recent post date (reverse chronological)
        sorted_topics = sorted(topics, key=self.get_latest_post_date, reverse=True)
        
        for topic in sorted_topics:
            filename = self.sanitize_filename(topic['title'])
            created_date = self.format_date(topic['date'])
            latest_date = self.format_date(topic['posts'][-1]['date']) if topic.get('posts') else created_date
            
            # Create entry with wiki link
            md.append(f"- [[{forum_name}/{filename}|{topic['title']}]]")
            md.append(f"  - Created: *{created_date}* by {topic['author']}")
            md.append(f"  - Last Activity: *{latest_date}*")
            if topic.get('posts'):
                md.append(f"  - Replies: {len(topic['posts']) - 1}")
            
            # Add tags if present
            if topic.get('tags'):
                md.append(f"  - Tags: {', '.join(['#'+self.sanitize_tag(t) for t in topic['tags']])}")
        
        # Write index file
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(md))
        
        print(f"Created {forum_name} index at {output_path}")
    
    def build_forum_vault(self, forum_data, forum_name, forum_path, base_url, unified_id_map):
        """Build vault from forum topics"""
        topics = forum_data['topics']
        
        for topic in topics:
            # Create filename from title
            filename = self.sanitize_filename(topic['title']) + '.md'
            filepath = os.path.join(forum_path, filename)
            
            # Generate markdown
            markdown = self.build_forum_topic(topic, unified_id_map, base_url)
            
            # Write file
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(markdown)
        
        # Create forum index
        index_path = os.path.join(self.vault_path, f'{forum_name} Archive.md')
        self.create_forum_index(topics, forum_name, index_path)
        
        print(f"Created {len(topics)} forum topic files in {forum_path}")


class DataStore:
    def __init__(self, conf):
        self.conf = conf
        self.root = conf['root']
        self.raw_archive = os.path.join(self.root, conf['local.storage']['raw'])
        os.makedirs(self.raw_archive, exist_ok=True)

    def update_archive(self):
        remote_files = self.conf['remote.raw_files']
        local_files = self.conf['local.raw_files']

        for f in remote_files:
            local = os.path.join(self.raw_archive, local_files[f])
            download_file(remote_files[f], local)

    def load_raw_file(self, f):
        path = os.path.join(self.raw_archive, self.conf['local.raw_files'][f])
        data = load_json(path)
        return data


# Instantiate an EntryPoints object
entry = EntryPoints()

@entry.point
def update_archive(args):
    conf = load_json(args.conf)
    DataStore(conf).update_archive()

@entry.point
def dump_item(args):
    conf = load_json(args.conf)
    ds = DataStore(conf)
    data = ds.load_raw_file('blog')
    print(len(data['posts']))
    data = ds.load_raw_file('general_forum')
    print(len(data['topics']))
    data = ds.load_raw_file('fvp_forum')
    print(len(data['topics']))

@entry.point
def build_vault(args):
    """Build an Obsidian vault from the archived data"""
    conf = load_json(args.conf)
    ds = DataStore(conf)
    builder = ObsidianVaultBuilder(conf)
    
    # Load all data
    blog_data = ds.load_raw_file('blog')
    fvp_forum_data = ds.load_raw_file('fvp_forum')
    general_forum_data = ds.load_raw_file('general_forum')
    
    # Apply max_posts limit if specified
    if args.max_posts is not None:
        blog_data['posts'] = blog_data['posts'][:args.max_posts]
        fvp_forum_data['topics'] = fvp_forum_data['topics'][:args.max_posts]
        general_forum_data['topics'] = general_forum_data['topics'][:args.max_posts]
    
    # Build unified ID map across all content
    unified_id_map = builder.build_unified_id_map(blog_data, fvp_forum_data, general_forum_data)
    
    # Build blog with unified map
    builder.build_blog_vault(blog_data, unified_id_map)
    
    # Build FVP Forum with unified map
    fvp_base_url = fvp_forum_data['topics'][0]['url'] if fvp_forum_data['topics'] else 'http://markforster.squarespace.com'
    builder.build_forum_vault(fvp_forum_data, 'FVP Forum', builder.fvp_forum_path, fvp_base_url, unified_id_map)
    
    # Build General Forum with unified map
    general_base_url = general_forum_data['topics'][0]['url'] if general_forum_data['topics'] else 'http://markforster.squarespace.com'
    builder.build_forum_vault(general_forum_data, 'General Forum', builder.general_forum_path, general_base_url, unified_id_map)
    
    print(f"Vault created at: {builder.vault_path}")

@entry.point
def build_html(args):
    """Build a standalone HTML site from the archived data"""
    conf = load_json(args.conf)
    ds = DataStore(conf)
    builder = HTMLSiteBuilder(conf)
    
    # Load all data
    blog_data = ds.load_raw_file('blog')
    fvp_forum_data = ds.load_raw_file('fvp_forum')
    general_forum_data = ds.load_raw_file('general_forum')
    
    # Apply max_posts limit if specified
    if args.max_posts is not None:
        blog_data['posts'] = blog_data['posts'][:args.max_posts]
        fvp_forum_data['topics'] = fvp_forum_data['topics'][:args.max_posts]
        general_forum_data['topics'] = general_forum_data['topics'][:args.max_posts]
    
    # Build unified URL map across all content
    unified_url_map = builder.build_unified_url_map(blog_data, fvp_forum_data, general_forum_data)
    
    # Build blog
    builder.build_blog_html(blog_data, unified_url_map)
    
    # Build forums
    fvp_base_url = fvp_forum_data['topics'][0]['url'] if fvp_forum_data['topics'] else 'http://markforster.squarespace.com'
    builder.build_forum_html(fvp_forum_data, 'fvp_forum', 'FVP Forum', fvp_base_url, unified_url_map)
    
    general_base_url = general_forum_data['topics'][0]['url'] if general_forum_data['topics'] else 'http://markforster.squarespace.com'
    builder.build_forum_html(general_forum_data, 'general_forum', 'General Forum', general_base_url, unified_url_map)
    
    # Build index pages
    builder.build_blog_index_html(blog_data)
    builder.build_forum_index_html(fvp_forum_data, 'fvp_forum', 'FVP Forum')
    builder.build_forum_index_html(general_forum_data, 'general_forum', 'General Forum')
    builder.build_main_index_html(blog_data, fvp_forum_data, general_forum_data)
    
    print(f"HTML site created at: {builder.html_path}")

@build_html.parser
def build_html_parser(parser):
    parser.add_argument("--max_posts", default=None, type=int)

@build_vault.parser
def build_vault_parser(parser):
    parser.add_argument("--max_posts", default=None, type=int)

@entry.add_common_parser
def common_settings(parser):
    parser.add_argument("--conf", default='conf.json')



if __name__=="__main__":
    entry.main()