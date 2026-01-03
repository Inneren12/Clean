"""
Repository security scan tests.

These tests scan the codebase for security issues and hardcoded values
that should not appear in runtime code.
"""

import os
import re
from pathlib import Path

import pytest


def test_no_example_com_in_runtime_code():
    """
    Verify that 'example.com' does not appear in runtime code paths.

    Allowlist:
    - Problem Details type URIs (RFC 7807) - these are namespace identifiers, not real URLs
    - Test files
    - Documentation files
    - Example configuration files
    - Scripts
    """
    repo_root = Path(__file__).parent.parent

    # Patterns to search for
    pattern = re.compile(r'example\.com', re.IGNORECASE)

    # Allowlisted files (relative to repo root)
    allowlist = {
        # Problem Details type URIs (RFC 7807) - namespace identifiers
        'app/main.py',           # PROBLEM_TYPE_* constants
        'app/api/problem_details.py',
        'app/domain/errors.py',  # DomainError.type default

        # Example/template files
        '.env.example',
        '.env.production.example',

        # Documentation
        'README.md',
        'docs',  # All docs are allowlisted
        'PRODUCTION_READINESS_AUDIT_2024-12-31.md',
        'PRODUCTION_READINESS_AUDIT_2025-12-31.md',
        'PRODUCTION_READINESS_AUDIT_2026-01-01.md',

        # CI/CD
        '.github',

        # Scripts
        'scripts',

        # Tests
        'tests',
    }

    violations = []

    # Search in app/ directory (runtime code)
    app_dir = repo_root / 'app'
    if app_dir.exists():
        for py_file in app_dir.rglob('*.py'):
            rel_path = py_file.relative_to(repo_root)

            # Check if file is allowlisted
            is_allowlisted = False
            for allowed in allowlist:
                if str(rel_path) == allowed or str(rel_path).startswith(allowed + '/'):
                    is_allowlisted = True
                    break

            if is_allowlisted:
                continue

            # Scan file content
            try:
                content = py_file.read_text(encoding='utf-8')
                for line_num, line in enumerate(content.splitlines(), start=1):
                    # Skip lines that are clearly safe
                    if '# PROBLEM_TYPE' in line or 'type:' in line and 'https://example.com/problems/' in line:
                        # RFC 7807 Problem Details type URIs are safe
                        continue

                    if pattern.search(line):
                        violations.append({
                            'file': str(rel_path),
                            'line': line_num,
                            'content': line.strip()
                        })
            except Exception as e:
                # Skip files that can't be read
                print(f"Warning: Could not read {rel_path}: {e}")
                continue

    # Report violations
    if violations:
        error_msg = "\n\nFound 'example.com' in runtime code (not allowlisted):\n\n"
        for v in violations:
            error_msg += f"  {v['file']}:{v['line']}\n    {v['content']}\n\n"
        error_msg += "\nThese occurrences should be replaced with actual production URLs or removed.\n"
        error_msg += "If these are legitimate (e.g., RFC 7807 type URIs), add them to the allowlist in this test.\n"
        pytest.fail(error_msg)


def test_no_hardcoded_secrets():
    """
    Verify that common secret patterns do not appear in code.

    This is a basic sanity check - not a comprehensive secret scanner.
    """
    repo_root = Path(__file__).parent.parent

    # Patterns that look like hardcoded secrets
    secret_patterns = [
        (re.compile(r'password\s*=\s*["\'][^"\']{8,}["\']', re.IGNORECASE), 'hardcoded password'),
        (re.compile(r'api[_-]?key\s*=\s*["\'][^"\']{16,}["\']', re.IGNORECASE), 'hardcoded API key'),
        (re.compile(r'secret[_-]?key\s*=\s*["\'][^"\']{16,}["\']', re.IGNORECASE), 'hardcoded secret key'),
        (re.compile(r'bearer\s+[a-zA-Z0-9_-]{20,}', re.IGNORECASE), 'hardcoded bearer token'),
    ]

    # Allowlisted files
    allowlist = {
        'tests',           # Test files can have test credentials
        '.env.example',
        '.env.production.example',
        'docs',
        'scripts',
    }

    violations = []

    # Search in app/ directory
    app_dir = repo_root / 'app'
    if app_dir.exists():
        for py_file in app_dir.rglob('*.py'):
            rel_path = py_file.relative_to(repo_root)

            # Check if file is allowlisted
            is_allowlisted = False
            for allowed in allowlist:
                if str(rel_path).startswith(allowed + '/') or str(rel_path) == allowed:
                    is_allowlisted = True
                    break

            if is_allowlisted:
                continue

            # Scan file content
            try:
                content = py_file.read_text(encoding='utf-8')
                for line_num, line in enumerate(content.splitlines(), start=1):
                    # Skip comments and docstrings (these often have examples)
                    stripped = line.strip()
                    if stripped.startswith('#') or stripped.startswith('"""') or stripped.startswith("'''"):
                        continue

                    # Check against secret patterns
                    for pattern, description in secret_patterns:
                        if pattern.search(line):
                            # Additional safeguards - skip if it looks like a test/example
                            if 'test' in line.lower() or 'example' in line.lower() or 'fake' in line.lower():
                                continue
                            if 'None' in line or 'getenv' in line or 'environ' in line:
                                continue

                            violations.append({
                                'file': str(rel_path),
                                'line': line_num,
                                'type': description,
                                'content': line.strip()
                            })
            except Exception as e:
                print(f"Warning: Could not read {rel_path}: {e}")
                continue

    # Report violations
    if violations:
        error_msg = "\n\nFound potential hardcoded secrets in runtime code:\n\n"
        for v in violations:
            error_msg += f"  {v['file']}:{v['line']} - {v['type']}\n    {v['content']}\n\n"
        error_msg += "\nSecrets should be loaded from environment variables or configuration files.\n"
        pytest.fail(error_msg)


def test_no_debug_endpoints_in_production():
    """
    Verify that debug/development endpoints are properly gated by environment checks.
    """
    repo_root = Path(__file__).parent.parent

    # Patterns for debug endpoints
    debug_patterns = [
        re.compile(r'@router\.(get|post|put|delete|patch)\(["\'].*/(debug|test|dev)/', re.IGNORECASE),
    ]

    violations = []

    # Search in app/api/ directory
    api_dir = repo_root / 'app' / 'api'
    if api_dir.exists():
        for py_file in api_dir.rglob('*.py'):
            rel_path = py_file.relative_to(repo_root)

            try:
                content = py_file.read_text(encoding='utf-8')
                lines = content.splitlines()

                for line_num, line in enumerate(lines, start=1):
                    for pattern in debug_patterns:
                        if pattern.search(line):
                            # Check if there's an environment check nearby (within 10 lines)
                            context_start = max(0, line_num - 10)
                            context_end = min(len(lines), line_num + 5)
                            context = '\n'.join(lines[context_start:context_end])

                            # Look for environment checks
                            has_guard = any([
                                'app_env' in context and ('dev' in context or 'development' in context),
                                'settings.testing' in context,
                                'if not prod' in context.lower(),
                                'APP_ENV' in context,
                            ])

                            if not has_guard:
                                violations.append({
                                    'file': str(rel_path),
                                    'line': line_num,
                                    'content': line.strip()
                                })
            except Exception as e:
                print(f"Warning: Could not read {rel_path}: {e}")
                continue

    # Report violations
    if violations:
        error_msg = "\n\nFound debug endpoints without environment guards:\n\n"
        for v in violations:
            error_msg += f"  {v['file']}:{v['line']}\n    {v['content']}\n\n"
        error_msg += "\nDebug endpoints should be gated by environment checks (e.g., app_env != 'prod').\n"
        pytest.fail(error_msg)
