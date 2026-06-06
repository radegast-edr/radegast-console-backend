"""Pack validation module for uploaded pack zip files."""

import io
import re
import zipfile
from pathlib import Path
from typing import Any

import yaml

# Allowed files in ioc directory
ALLOWED_IOC_FILES = {
    "paths_regex.txt",
    "ips.txt",
    "hashes.txt",
    "domains.txt",
    "files.txt",
}

# Sigma rule schema - simplified validation for required fields
# Minimal required fields for a valid Sigma rule
SIGMA_REQUIRED_FIELDS = {"title", "detection"}


def validate_yaml_syntax(content: str, filename: str) -> list[str]:
    """Validate YAML syntax."""
    errors = []
    try:
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        errors.append(f"{filename}: Invalid YAML syntax - {str(e)}")
    return errors


def validate_sigma_rule(content: dict[str, Any], filename: str) -> list[str]:
    """Validate a Sigma rule has required fields."""
    errors = []
    
    if not isinstance(content, dict):
        errors.append(f"{filename}: Sigma rule must be a YAML mapping, not a {type(content).__name__}")
        return errors
    
    # Check for required fields
    missing_fields = SIGMA_REQUIRED_FIELDS - set(content.keys())
    if missing_fields:
        errors.append(f"{filename}: Missing required Sigma fields: {', '.join(sorted(missing_fields))}")
    
    # Validate logsource if present
    if "logsource" in content:
        logsource = content["logsource"]
        if not isinstance(logsource, dict):
            errors.append(f"{filename}: logsource must be a mapping")
        elif "category" not in logsource:
            errors.append(f"{filename}: logsource.category is required")
    
    # Validate detection if present
    if "detection" in content:
        detection = content["detection"]
        if isinstance(detection, dict):
            if not detection:
                errors.append(f"{filename}: detection must have at least one detection rule")
        elif isinstance(detection, list):
            if not detection:
                errors.append(f"{filename}: detection list must not be empty")
        else:
            errors.append(f"{filename}: detection must be a mapping or list")
    
    return errors


def validate_yara_rule(content: str, filename: str) -> list[str]:
    """Validate YARA rule syntax (basic check)."""
    errors = []
    
    # Basic YARA syntax checks
    # Remove comments
    lines = []
    for line in content.split('\n'):
        stripped = line.strip()
        if stripped and not stripped.startswith('//') and not stripped.startswith('#'):
            lines.append(stripped)
    
    content_no_comments = '\n'.join(lines)
    
    # Check if file is empty
    if not content_no_comments.strip():
        errors.append(f"{filename}: YARA file is empty")
        return errors
    
    # Check for rule declaration pattern
    rule_pattern = re.compile(r'\brule\s+\w+\s*\{')
    if not rule_pattern.search(content):
        errors.append(f"{filename}: No valid YARA rule declaration found (expected 'rule RuleName {{')")
    
    # Check for balanced braces
    open_braces = content.count('{')
    close_braces = content.count('}')
    if open_braces != close_braces:
        errors.append(f"{filename}: Unbalanced braces in YARA file ({open_braces} open, {close_braces} close)")
    
    return errors


def validate_ioc_file_content(content: str, filename: str) -> list[str]:
    """Validate IOC file content based on filename."""
    errors = []
    
    # Check if file is empty
    if not content.strip():
        errors.append(f"{filename}: File is empty")
    
    return errors


async def validate_zip_contents(zip_data: bytes) -> dict[str, Any]:
    """
    Validate the contents of an uploaded pack zip file.
    
    Returns:
        dict with 'valid' (bool), 'errors' (list of str), 'warnings' (list of str), and 'meta' (dict or None)
    """
    errors: list[str] = []
    warnings: list[str] = []
    meta: dict[str, Any] | None = None
    
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            file_list = zf.namelist()
            
            # Check if zip is empty
            if not file_list:
                errors.append("Zip file is empty")
                return {"valid": False, "errors": errors, "warnings": warnings, "meta": meta}
            
            # Check for required directories
            has_ioc = any(f.startswith("ioc/") or f == "ioc" for f in file_list)
            has_sigma = any(f.startswith("sigma/") or f == "sigma" for f in file_list)
            has_yara = any(f.startswith("yara/") or f == "yara" for f in file_list)
            
            if not (has_ioc or has_sigma or has_yara):
                errors.append("Zip must contain at least one of: ioc/, sigma/, yara/ directories")
            
            # Separate files by category
            top_level_files: list[str] = []
            ioc_files: list[str] = []
            sigma_files: list[str] = []
            yara_files: list[str] = []
            other_files: list[str] = []
            
            for f in file_list:
                if f == "pack.yml":
                    top_level_files.append(f)
                elif f.startswith("ioc/"):
                    ioc_files.append(f)
                elif f.startswith("sigma/"):
                    sigma_files.append(f)
                elif f.startswith("yara/"):
                    yara_files.append(f)
                else:
                    other_files.append(f)
            
            # Check for unexpected files at top level
            for f in top_level_files:
                if f != "pack.yml":
                    errors.append(f"Unexpected top-level file: {f}. Only pack.yml is allowed at top level")
            
            # Check for unexpected files in directories
            for f in other_files:
                if not f.endswith('/'):  # Skip directory entries
                    errors.append(f"Unexpected file: {f}. Files must be in ioc/, sigma/, or yara/ directories")
            
            # Validate ioc directory files
            for f in ioc_files:
                if not f.endswith('/'):  # Skip directory entries
                    basename = Path(f).name
                    if basename not in ALLOWED_IOC_FILES:
                        errors.append(f"Unexpected file in ioc/: {basename}. Allowed: {', '.join(sorted(ALLOWED_IOC_FILES))}")
            
            # Validate sigma directory files
            for f in sigma_files:
                if not f.endswith('/'):  # Skip directory entries
                    if not (f.endswith('.yml') or f.endswith('.yaml')):
                        errors.append(f"File in sigma/ must have .yml or .yaml extension: {f}")
            
            # Validate yara directory files
            for f in yara_files:
                if not f.endswith('/'):  # Skip directory entries
                    if not f.endswith('.yar'):
                        errors.append(f"File in yara/ must have .yar extension: {f}")
            
            # If we have errors so far, return early
            if errors:
                return {"valid": False, "errors": errors, "warnings": warnings, "meta": meta}
            
            # Now validate file contents
            # Process pack.yml first
            pack_yml_content: dict[str, Any] | None = None
            if "pack.yml" in file_list:
                try:
                    with zf.open("pack.yml") as pf:
                        pack_yml_text = pf.read().decode('utf-8')
                        pack_yml_content = yaml.safe_load(pack_yml_text)
                        
                        # Validate pack.yml syntax
                        yaml_errors = validate_yaml_syntax(pack_yml_text, "pack.yml")
                        errors.extend(yaml_errors)
                except Exception as e:
                    errors.append(f"Failed to read pack.yml: {str(e)}")
            
            # Validate sigma files
            for f in sigma_files:
                if not f.endswith('/'):  # Skip directory entries
                    try:
                        with zf.open(f) as sf:
                            content_text = sf.read().decode('utf-8')
                            
                            # Check YAML syntax first
                            yaml_errors = validate_yaml_syntax(content_text, f)
                            errors.extend(yaml_errors)
                            
                            # If YAML is valid, check Sigma rule structure
                            if not yaml_errors:
                                try:
                                    content_dict = yaml.safe_load(content_text)
                                    sigma_errors = validate_sigma_rule(content_dict, f)
                                    errors.extend(sigma_errors)
                                except Exception as e:
                                    errors.append(f"{f}: Failed to parse as Sigma rule: {str(e)}")
                    except Exception as e:
                        errors.append(f"Failed to read {f}: {str(e)}")
            
            # Validate yara files
            for f in yara_files:
                if not f.endswith('/'):  # Skip directory entries
                    try:
                        with zf.open(f) as yf:
                            content_text = yf.read().decode('utf-8')
                            yara_errors = validate_yara_rule(content_text, f)
                            errors.extend(yara_errors)
                    except Exception as e:
                        errors.append(f"Failed to read {f}: {str(e)}")
            
            # Validate ioc files
            for f in ioc_files:
                if not f.endswith('/'):  # Skip directory entries
                    try:
                        with zf.open(f) as ifile:
                            content_text = ifile.read().decode('utf-8')
                            ioc_errors = validate_ioc_file_content(content_text, f)
                            errors.extend(ioc_errors)
                    except Exception as e:
                        errors.append(f"Failed to read {f}: {str(e)}")

            # If pack.yml was found and is valid, use it as meta
            if pack_yml_content is not None and not errors:
                meta = pack_yml_content
            
            return {
                "valid": len(errors) == 0,
                "errors": errors,
                "warnings": warnings,
                "meta": meta
            }
    
    except zipfile.BadZipFile:
        errors.append("Invalid zip file format")
        return {"valid": False, "errors": errors, "warnings": warnings, "meta": meta}
    except Exception as e:
        errors.append(f"Unexpected error validating zip: {str(e)}")
        return {"valid": False, "errors": errors, "warnings": warnings, "meta": meta}
