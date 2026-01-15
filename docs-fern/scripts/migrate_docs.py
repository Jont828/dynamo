#!/usr/bin/env python3
"""
Docusaurus to Fern Migration Script

Converts Docusaurus markdown files to Fern-compatible MDX format.
- Removes Docusaurus frontmatter (slug, sidebar_position)
- Converts HTML copyright headers to JSX-style comments
- Converts admonitions (:::tip, :::warning, etc.) to Fern <Callout> components
- Preserves content structure and code blocks
"""

import os
import re
import sys
from pathlib import Path

# JSX-style copyright header for Fern MDX files
COPYRIGHT_HEADER = """{/*
  SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
  SPDX-License-Identifier: Apache-2.0
*/}

"""

def strip_copyright_headers(content: str) -> str:
    """Remove HTML comment copyright headers that cause Fern MDX parsing errors.
    
    These headers look like:
    <!--
    SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES.
    ...
    -->
    
    The JSX-style copyright will be added after all conversions in convert_file().
    """
    # Remove HTML comment blocks at the start of the file
    content = re.sub(r'^\s*<!--[\s\S]*?-->\s*', '', content)
    # Also remove HTML comment blocks that appear after frontmatter (common in Docusaurus)
    # This handles cases where <!-- appears right after the frontmatter closing ---
    content = re.sub(r'\n\s*<!--[\s\S]*?-->\s*\n', '\n', content)
    return content

def add_jsx_copyright(content: str) -> str:
    """Add JSX-style copyright header after frontmatter.
    
    Fern MDX doesn't support HTML comments, so we use JSX-style: {/* ... */}
    """
    # Check if already has JSX copyright
    if '{/*' in content and 'SPDX' in content:
        return content
    
    # Find end of frontmatter and insert copyright after it
    match = re.match(r'^(---\n.*?\n---\n)', content, re.DOTALL)
    if match:
        frontmatter = match.group(1)
        rest = content[len(frontmatter):].lstrip('\n')
        return frontmatter + '\n' + COPYRIGHT_HEADER + rest
    else:
        # No frontmatter, add copyright at start
        return COPYRIGHT_HEADER + content

def convert_frontmatter(content: str) -> str:
    """Remove Docusaurus-specific frontmatter fields, keep title."""
    # Match frontmatter block
    frontmatter_match = re.match(r'^---\n(.*?)\n---\n', content, flags=re.DOTALL)
    if not frontmatter_match:
        return content
    
    # Remove the entire frontmatter block - Fern uses docs.yml for navigation
    return re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL)

def convert_title_to_frontmatter(content: str) -> str:
    """Convert first # Heading to YAML frontmatter title.
    
    Converts:
        # My Page Title
        Content here...
    
    To:
        ---
        title: "My Page Title"
        ---
        
        Content here...
    """
    # Strip leading whitespace first
    content = content.lstrip()
    
    # Match the first # heading (not ## or deeper)
    match = re.match(r'^# (.+?)[\n\r]', content)
    if not match:
        return content
    
    title = match.group(1).strip()
    # Escape quotes in title
    title = title.replace('"', '\\"')
    
    # Remove the # heading line
    content = re.sub(r'^# .+?[\n\r]+', '', content, count=1)
    
    # Add frontmatter
    return f'---\ntitle: "{title}"\n---\n\n{content}'

def convert_admonitions(content: str) -> str:
    """Convert Docusaurus :::type admonitions to Fern <Callout> components."""
    
    # Mapping of Docusaurus admonition types to Fern Callout intents
    mapping = {
        'tip': 'success',
        'note': 'info',
        'info': 'info',
        'warning': 'warning',
        'danger': 'danger',
        'caution': 'warning',
        'important': 'warning',
    }
    
    # Pattern to match admonitions with optional title
    # :::type Title (optional)
    # content
    # :::
    pattern = r':::(\w+)(?:\s+(.+?))?\n(.*?):::'
    
    def replace_admonition(match):
        admon_type = match.group(1).lower()
        title = match.group(2)
        content = match.group(3).strip()
        
        intent = mapping.get(admon_type, 'info')
        
        if title:
            return f'<Callout intent="{intent}">\n**{title}**\n\n{content}\n</Callout>'
        else:
            return f'<Callout intent="{intent}">\n{content}\n</Callout>'
    
    content = re.sub(pattern, replace_admonition, content, flags=re.DOTALL)
    
    # Also convert GitHub-style admonitions: > [!NOTE], > [!TIP], etc.
    # Use case-insensitive matching
    github_mapping = {
        'note': 'info',
        'tip': 'success',
        'important': 'warning',
        'warning': 'warning',
        'caution': 'danger',
    }
    
    # Match > [!TYPE] followed by lines starting with >
    # Handle both > [!Note] and > [!NOTE] (case insensitive)
    for gh_type, intent in github_mapping.items():
        # Pattern matches the admonition header and all following > lines
        pattern = rf'> \[!{gh_type}\]\n((?:>.*\n?)*)'
        def replace_github(match, intent=intent):
            lines = match.group(1).strip().split('\n')
            content_lines = []
            for line in lines:
                # Remove leading > and optional space
                cleaned = line.lstrip('>').lstrip(' ').rstrip()
                if cleaned:  # Only add non-empty lines
                    content_lines.append(cleaned)
            content = '\n'.join(content_lines)
            return f'<Callout intent="{intent}">\n{content}\n</Callout>\n'
        content = re.sub(pattern, replace_github, content, flags=re.IGNORECASE)
    
    return content

def convert_file(src_path: str, dest_path: str) -> bool:
    """Convert a single markdown file from Docusaurus to Fern format."""
    try:
        with open(src_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Apply conversions in order
        content = strip_copyright_headers(content)
        content = convert_frontmatter(content)
        content = convert_title_to_frontmatter(content)
        content = convert_admonitions(content)
        
        # Fix image paths: /img/... -> relative path to assets/img/
        # Calculate relative path from destination to assets/img
        # For now, use a simple replacement that works for most cases
        content = re.sub(r'\]\(/img/', '](../../assets/img/', content)
        
        # Add JSX-style copyright header (after frontmatter)
        content = add_jsx_copyright(content)
        
        # Ensure destination has .mdx extension
        if dest_path.endswith('.md'):
            dest_path = dest_path[:-3] + '.mdx'
        
        # Create destination directory if needed
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        
        with open(dest_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return True
    except Exception as e:
        print(f"Error converting {src_path}: {e}", file=sys.stderr)
        return False

def main():
    if len(sys.argv) < 3:
        print("Usage: migrate_docs.py <source_file> <dest_file>")
        print("   or: migrate_docs.py <source_dir> <dest_dir> --batch")
        sys.exit(1)
    
    src = sys.argv[1]
    dest = sys.argv[2]
    
    if len(sys.argv) > 3 and sys.argv[3] == '--batch':
        # Batch mode - convert entire directory
        src_dir = Path(src)
        dest_dir = Path(dest)
        
        success_count = 0
        fail_count = 0
        
        for src_file in src_dir.rglob('*.md'):
            rel_path = src_file.relative_to(src_dir)
            dest_file = dest_dir / rel_path
            dest_file = dest_file.with_suffix('.mdx')
            
            if convert_file(str(src_file), str(dest_file)):
                print(f"✓ {rel_path}")
                success_count += 1
            else:
                print(f"✗ {rel_path}")
                fail_count += 1
        
        print(f"\nConverted {success_count} files, {fail_count} failures")
    else:
        # Single file mode
        if convert_file(src, dest):
            print(f"✓ Converted {src} -> {dest}")
        else:
            print(f"✗ Failed to convert {src}")
            sys.exit(1)

if __name__ == "__main__":
    main()
