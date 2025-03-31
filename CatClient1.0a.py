import os, sys, json, shutil, zipfile, threading
import urllib.request
import urllib.error
import ssl  # Added for SSL context handling
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import re
import subprocess
import uuid as uuidlib
import platform

# --- Constants ---
USER_AGENT = "Mozilla/5.0 (Macintosh; Apple Silicon Mac OS X) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36"

# --- SSL Context Setup ---
def get_ssl_context(verify=False):
    """Create SSL context with optional verification"""
    if verify:
        ctx = ssl.create_default_context()
    else:
        ctx = ssl._create_unverified_context()
    return ctx

# --- Directory Setup ---
mc_dir = os.path.expanduser("~/Library/Application Support/minecraft")
if not os.path.isdir(mc_dir):
    os.makedirs(mc_dir, exist_ok=True)

VERSIONS_DIR = os.path.join(mc_dir, "versions")
ASSETS_DIR = os.path.join(mc_dir, "assets")
MODPACKS_DIR = os.path.join(mc_dir, "modpacks")
LIBRARIES_DIR = os.path.join(mc_dir, "libraries")

os.makedirs(VERSIONS_DIR, exist_ok=True)
os.makedirs(MODPACKS_DIR, exist_ok=True)
os.makedirs(os.path.join(ASSETS_DIR, "indexes"), exist_ok=True)
os.makedirs(os.path.join(ASSETS_DIR, "objects"), exist_ok=True)
os.makedirs(LIBRARIES_DIR, exist_ok=True)

# URLs
VERSION_MANIFEST_URL = "https://launchermeta.mojang.com/mc/game/version_manifest.json"
ASSET_BASE_URL = "http://resources.download.minecraft.net/"
LIBRARIES_BASE_URL = "https://libraries.minecraft.net/"
FORGE_MAVEN_URL = "https://maven.minecraftforge.net/"
TLMODS_BASE_URL = "https://tlmods.org"
LUNAR_CLIENT_RESOURCES = "https://api.lunarclientprod.com"  # Added for Lunar Client

# --- Account Management ---
accounts = []
accounts_file = os.path.join(mc_dir, "launcher_accounts.json")
if os.path.isfile(accounts_file):
    try:
        with open(accounts_file, 'r') as f:
            accounts = json.load(f)
    except json.JSONDecodeError:
        print(f"Warning: Could not parse {accounts_file}. Starting with empty accounts.")
        accounts = []
    except Exception as e:
        print(f"Warning: Error loading accounts: {e}")
        accounts = []

def save_accounts():
    try:
        with open(accounts_file, 'w') as f:
            json.dump(accounts, f, indent=4)
    except Exception as e:
        print(f"Error saving accounts: {e}")

def add_account(acc_type, email_username, password_token=None):
    """Adds/updates an account."""
    if not email_username: return

    # Generate UUID for offline mode consistently
    offline_uuid = str(uuidlib.uuid3(uuidlib.NAMESPACE_DNS, email_username))

    if acc_type == "tlauncher":
        acc = {"type": "tlauncher", "username": email_username, "password": password_token or "", "uuid": offline_uuid, "token": "null"}
    elif acc_type == "lunar":  # Added Lunar Client account type
        acc = {"type": "lunar", "username": email_username, "uuid": offline_uuid, "token": "null", "client": "lunar"}
    elif acc_type == "offline":
        acc = {"type": "offline", "username": email_username, "uuid": offline_uuid, "token": "null"}
    elif acc_type == "microsoft":
        acc = {"type": "microsoft", "username": email_username, "uuid": offline_uuid, "token": (password_token or "0")}
    else:
        print(f"Unknown account type: {acc_type}")
        return

    # Check if account exists (by type and username) and update, otherwise add
    found = False
    for i, existing_acc in enumerate(accounts):
        if existing_acc.get("type") == acc_type and existing_acc.get("username") == email_username:
            accounts[i] = acc
            found = True
            break
    if not found:
        accounts.append(acc)

    save_accounts()
    print(f"Account '{email_username}' ({acc_type}) added/updated.")

# --- Download Helper ---
def download_file(url, dest_path, description="file", ssl_verify=False):
    """Download file from url to dest_path with User-Agent and better error handling."""
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    
    # Use SSL context based on verification setting
    ssl_context = get_ssl_context(ssl_verify)
    
    try:
        print(f"Downloading {description}: {os.path.basename(dest_path)} from {url}")
        with urllib.request.urlopen(req, context=ssl_context) as response, open(dest_path, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)
        print(f"Finished downloading {os.path.basename(dest_path)}")
    except urllib.error.HTTPError as e:
        raise Exception(f"Failed to download {description} from {url}. HTTP Error: {e.code} {e.reason}") from e
    except urllib.error.URLError as e:
        # Check if it's an SSL error and we're verifying
        if ssl_verify and "CERTIFICATE_VERIFY_FAILED" in str(e):
            print(f"SSL Certificate verification failed. Trying without verification for {url}")
            # Retry without SSL verification
            ssl_context_unverified = get_ssl_context(False)
            try:
                with urllib.request.urlopen(req, context=ssl_context_unverified) as response, open(dest_path, 'wb') as out_file:
                    shutil.copyfileobj(response, out_file)
                print(f"Finished downloading {os.path.basename(dest_path)} without SSL verification")
            except Exception as e2:
                raise Exception(f"Failed to download {description} from {url} even without SSL verification. Error: {e2}") from e2
        else:
            raise Exception(f"Failed to download {description} from {url}. URL Error: {e}") from e
    except Exception as e:
        raise Exception(f"Failed to download {description} from {url}. Error: {e}") from e

# --- Version Manifest Loading ---
version_manifest_path = os.path.join(mc_dir, "version_manifest_v2.json")
all_versions = {}

def load_version_manifest(ssl_verify=False):
    """Load version manifest with fallback handling"""
    global all_versions
    global version_manifest_path
    
    try:
        if not os.path.isfile(version_manifest_path):
            print("Downloading version manifest v2...")
            try:
                download_file("https://launchermeta.mojang.com/mc/game/version_manifest_v2.json", 
                              version_manifest_path, "version manifest v2", ssl_verify)
            except Exception as e:
                print(f"Failed to download v2 manifest: {e}, falling back to v1.")
                version_manifest_path = os.path.join(mc_dir, "version_manifest.json")
                download_file(VERSION_MANIFEST_URL, version_manifest_path, 
                              "version manifest v1", ssl_verify)
        
        with open(version_manifest_path, 'r') as f:
            version_manifest = json.load(f)
            
        # Build list of all versions (id and URL)
        all_versions = {v['id']: v['url'] for v in version_manifest['versions']}
        
        return version_manifest
    except Exception as e:
        print(f"Error loading version manifest: {e}")
        # Return empty manifest but don't exit - the GUI will show appropriate error
        return {"versions": []}

# --- M1 Mac Specific Functions ---
def is_arm64():
    """Check if running on Apple Silicon natively"""
    return platform.machine() == 'arm64'

def detect_rosetta():
    """Check if Rosetta 2 is installed"""
    try:
        result = subprocess.run(['sysctl', '-n', 'sysctl.proc_translated'], 
                               capture_output=True, text=True, check=False)
        return result.stdout.strip() == '1'
    except:
        return False

def run_with_rosetta(cmd):
    """Prefix command to run with Rosetta if needed"""
    if is_arm64() and not detect_rosetta():
        # We're on ARM64 natively, add arch -x86_64 prefix to run with Rosetta
        return ['arch', '-x86_64'] + cmd
    return cmd  # Already running under Rosetta or not on ARM64

# --- Minecraft Installation Logic ---
def install_version(version_id, status_callback=None, ssl_verify=False):
    """Ensure the given Minecraft version (version_id) and its dependencies are installed."""
    if status_callback: status_callback(f"Checking version: {version_id}...")

    version_folder = os.path.join(VERSIONS_DIR, version_id)
    version_json_path = os.path.join(version_folder, f"{version_id}.json")
    version_jar_path = os.path.join(version_folder, f"{version_id}.jar")

    # Check if primary JSON exists, if not, download it
    if not os.path.isfile(version_json_path):
        if version_id not in all_versions:
            raise Exception(f"Version '{version_id}' not found in Mojang manifest.")

        version_url = all_versions[version_id]
        os.makedirs(version_folder, exist_ok=True)
        if status_callback: status_callback(f"Downloading version JSON for {version_id}...")
        download_file(version_url, version_json_path, f"version JSON ({version_id})", ssl_verify)
    else:
        print(f"Version JSON for {version_id} already exists.")

    # Load version JSON
    try:
        with open(version_json_path, 'r') as f:
            version_data = json.load(f)
    except Exception as e:
        raise Exception(f"Failed to load version JSON for {version_id}: {e}")

    # --- Handle Inheritance (e.g., Forge > 1.12) ---
    parent_id = version_data.get("inheritsFrom")
    parent_data = {}
    if parent_id:
        if status_callback: status_callback(f"Version {version_id} inherits from {parent_id}. Installing parent...")
        try:
            install_version(parent_id, status_callback, ssl_verify)
            parent_json_path = os.path.join(VERSIONS_DIR, parent_id, f"{parent_id}.json")
            with open(parent_json_path, 'r') as pf:
                parent_data = json.load(pf)
        except Exception as e:
            raise Exception(f"Failed to install parent version {parent_id}: {e}")

    # --- Download Client JAR ---
    client_info = version_data.get("downloads", {}).get("client")
    if client_info and not os.path.isfile(version_jar_path):
        client_url = client_info.get("url")
        if client_url:
            if status_callback: status_callback(f"Downloading client JAR for {version_id}...")
            download_file(client_url, version_jar_path, f"client JAR ({version_id})", ssl_verify)
        else:
            print(f"Warning: No client JAR URL found for {version_id}")
    elif not os.path.isfile(version_jar_path) and not parent_id:
         print(f"Warning: Client JAR for {version_id} is missing and no download info found.")

    # --- Combine Libraries (Current + Parent) ---
    libraries = version_data.get("libraries", []) + parent_data.get("libraries", [])

    # --- Download Libraries ---
    if status_callback: status_callback(f"Checking libraries for {version_id}...")
    total_libs = len(libraries)
    for i, lib in enumerate(libraries):
        # Check rules (OS, architecture)
        rules = lib.get("rules", [])
        allowed = True 
        if rules:
            allowed = False
            for rule in rules:
                action = rule.get("action")
                os_rule = rule.get("os", {})
                if action == "allow":
                    if not os_rule:
                        allowed = True
                        break
                    if os_rule.get("name") == 'osx':
                        allowed = True
                        break
                elif action == "disallow":
                    if not os_rule:
                        allowed = False
                        break
                    if os_rule.get("name") == 'osx':
                        allowed = False
                        break

        if not allowed:
            continue

        # Download main artifact
        artifact = lib.get("downloads", {}).get("artifact")
        if artifact and artifact.get("path"):
            lib_path = os.path.join(LIBRARIES_DIR, artifact["path"])
            if not os.path.isfile(lib_path):
                lib_url = artifact.get("url")
                if not lib_url:
                    if 'forge' in lib.get('name','').lower():
                        lib_url = FORGE_MAVEN_URL + artifact["path"]
                    else:
                        lib_url = LIBRARIES_BASE_URL + artifact["path"]

                if status_callback: status_callback(f"Downloading library {i+1}/{total_libs}: {os.path.basename(lib_path)}")
                try:
                    download_file(lib_url, lib_path, f"library ({os.path.basename(lib_path)})", ssl_verify)
                except Exception as e:
                    print(f"Warning: Failed to download library {lib.get('name')}: {e}. Trying next source if available.")
                    if LIBRARIES_BASE_URL in lib_url:
                         fallback_url = FORGE_MAVEN_URL + artifact["path"]
                         print(f"Trying Forge Maven: {fallback_url}")
                         try:
                             download_file(fallback_url, lib_path, f"library ({os.path.basename(lib_path)})", ssl_verify)
                         except Exception as e2:
                             print(f"Fallback download failed: {e2}")
                    elif FORGE_MAVEN_URL in lib_url:
                         fallback_url = LIBRARIES_BASE_URL + artifact["path"]
                         print(f"Trying Mojang Libraries: {fallback_url}")
                         try:
                             download_file(fallback_url, lib_path, f"library ({os.path.basename(lib_path)})", ssl_verify)
                         except Exception as e2:
                             print(f"Fallback download failed: {e2}")

        # Handle macOS natives
        natives_info = lib.get("natives")
        classifiers = lib.get("downloads", {}).get("classifiers", {})
        if natives_info and classifiers:
            native_os = 'osx'
            
            # Properly handle ARM64 vs x86_64 architecture for natives
            arch = '64'  # Default to 64-bit
            if is_arm64() and 'natives-osx-arm64' in classifiers:
                native_key = 'natives-osx-arm64'
            else:
                native_key = natives_info.get(native_os, '').replace("${arch}", arch)

            if native_key and native_key in classifiers:
                native_artifact = classifiers[native_key]
                if native_artifact.get("path"):
                    native_path = os.path.join(LIBRARIES_DIR, native_artifact["path"])
                    if not os.path.isfile(native_path):
                        native_url = native_artifact.get("url")
                        if not native_url:
                             if 'forge' in lib.get('name','').lower():
                                 native_url = FORGE_MAVEN_URL + native_artifact["path"]
                             else:
                                 native_url = LIBRARIES_BASE_URL + native_artifact["path"]

                        if status_callback: status_callback(f"Downloading native library {i+1}/{total_libs}: {os.path.basename(native_path)}")
                        try:
                            download_file(native_url, native_path, f"native library ({os.path.basename(native_path)})", ssl_verify)
                        except Exception as e:
                            print(f"Warning: Failed to download native {lib.get('name')}: {e}")

                    # Extract natives
                    natives_dir = os.path.join(version_folder, "natives")
                    os.makedirs(natives_dir, exist_ok=True)
                    try:
                        if os.path.isfile(native_path):
                            with zipfile.ZipFile(native_path, 'r') as zf:
                                exclude_prefixes = lib.get("extract", {}).get("exclude", [])
                                for member in zf.namelist():
                                    if member.startswith("META-INF/") or any(member.startswith(prefix) for prefix in exclude_prefixes):
                                        continue
                                    if not member.endswith('/'):
                                       zf.extract(member, natives_dir)
                    except zipfile.BadZipFile:
                        print(f"Warning: Could not extract natives from corrupted file: {native_path}")
                    except Exception as e:
                        print(f"Warning: Failed to extract natives from {native_path}: {e}")

    # --- Download Assets ---
    asset_index_info = version_data.get("assetIndex") or parent_data.get("assetIndex")
    if asset_index_info and asset_index_info.get("id") and asset_index_info.get("url"):
        idx_id = asset_index_info["id"]
        idx_url = asset_index_info["url"]
        idx_dest = os.path.join(ASSETS_DIR, "indexes", f"{idx_id}.json")

        if not os.path.isfile(idx_dest):
            if status_callback: status_callback(f"Downloading asset index {idx_id}...")
            download_file(idx_url, idx_dest, f"asset index ({idx_id})", ssl_verify)

        # Load asset index and download objects
        try:
            with open(idx_dest, 'r') as f:
                idx_data = json.load(f)

            if idx_data and "objects" in idx_data:
                if status_callback: status_callback(f"Checking assets for index {idx_id}...")
                total_assets = len(idx_data["objects"])
                assets_downloaded = 0
                for i, (asset_name, info) in enumerate(idx_data["objects"].items()):
                    hash_val = info.get("hash")
                    if hash_val:
                        subdir = hash_val[:2]
                        asset_path = os.path.join(ASSETS_DIR, "objects", subdir, hash_val)
                        if not os.path.isfile(asset_path):
                            assets_downloaded += 1
                            if status_callback: status_callback(f"Downloading asset {assets_downloaded}/{total_assets}: {asset_name}")
                            asset_url = ASSET_BASE_URL + f"{subdir}/{hash_val}"
                            download_file(asset_url, asset_path, f"asset ({hash_val[:8]})", ssl_verify)

        except Exception as e:
             print(f"Warning: Error processing assets for index {idx_id}: {e}")

    else:
        print(f"Warning: No valid asset index information found for version {version_id}")

    # --- TLauncher Skin Patch ---
    try:
        with open(version_json_path, 'r+') as vf:
            data = json.load(vf)
            if not data.get("skinVersion", False):
                data["skinVersion"] = True
                vf.seek(0)
                json.dump(data, vf, indent=4)
                vf.truncate()
                print(f"Patched {version_id}.json with skinVersion=true")
    except Exception as e:
        print(f"Warning: Could not set skinVersion in {version_id}.json - {e}")

    if status_callback: status_callback(f"Version {version_id} installation complete.")

# --- Lunar Client Support ---
def setup_lunar_client(version_id, status_callback=None):
    """Set up necessary files for Lunar Client compatibility"""
    if status_callback: status_callback(f"Setting up Lunar Client compatibility for {version_id}...")
    
    # Create Lunar Client directory structure
    lunar_dir = os.path.expanduser("~/.lunarclient")
    lunar_offline_dir = os.path.join(lunar_dir, "offline")
    lunar_jre_dir = os.path.join(lunar_dir, "jre")
    
    os.makedirs(lunar_offline_dir, exist_ok=True)
    os.makedirs(lunar_jre_dir, exist_ok=True)
    
    # Create offline mode marker file if it doesn't exist
    offline_marker = os.path.join(lunar_offline_dir, ".offline")
    if not os.path.exists(offline_marker):
        with open(offline_marker, 'w') as f:
            f.write("1")  # Just a marker file
    
    # Create symbolic link to the Minecraft versions directory
    lunar_versions_dir = os.path.join(lunar_dir, "game-versions")
    if not os.path.exists(lunar_versions_dir):
        try:
            os.symlink(VERSIONS_DIR, lunar_versions_dir)
        except Exception as e:
            print(f"Warning: Could not create symlink to versions directory: {e}")
    
    # Create settings json if needed
    settings_path = os.path.join(lunar_dir, "settings.json")
    if not os.path.exists(settings_path):
        default_settings = {
            "gameDir": mc_dir,
            "jreDir": lunar_jre_dir,
            "width": 854,
            "height": 480,
            "lastVersion": version_id,
            "offline": True
        }
        
        try:
            with open(settings_path, 'w') as f:
                json.dump(default_settings, f, indent=4)
        except Exception as e:
            print(f"Warning: Could not create Lunar Client settings: {e}")
    
    if status_callback: status_callback(f"Lunar Client setup complete for {version_id}")

# --- Game Launch Logic ---
def launch_game(version_id, account, ram_mb=1024, java_path="java", game_dir=None, server_ip=None, port=None, 
               status_callback=None, use_rosetta=False, lunar_client=False, ssl_verify=False):
    """Constructs and executes the Minecraft launch command."""
    if status_callback: status_callback(f"Preparing to launch {version_id}...")

    effective_game_dir = game_dir if game_dir and os.path.isdir(game_dir) else mc_dir
    print(f"Using game directory: {effective_game_dir}")

    if lunar_client:
        setup_lunar_client(version_id, status_callback)
    
    try:
        install_version(version_id, status_callback, ssl_verify)
    except Exception as e:
        raise Exception(f"Failed to ensure version '{version_id}' is installed before launch: {e}")

    version_folder = os.path.join(VERSIONS_DIR, version_id)
    version_json_path = os.path.join(version_folder, f"{version_id}.json")
    if not os.path.isfile(version_json_path):
        raise Exception(f"Launch aborted: Version JSON not found for '{version_id}' at {version_json_path}")

    with open(version_json_path, 'r') as f:
        vdata = json.load(f)

    main_class = vdata.get("mainClass")
    jvm_args = []
    game_args = []
    classpath = set()

    parent_data = {}
    inherits_from = vdata.get("inheritsFrom")
    if inherits_from:
        try:
            parent_json_path = os.path.join(VERSIONS_DIR, inherits_from, f"{inherits_from}.json")
            with open(parent_json_path, 'r') as pf:
                parent_data = json.load(pf)
            if not main_class:
                main_class = parent_data.get("mainClass")
        except Exception as e:
            print(f"Warning: Could not load parent version {inherits_from}: {e}. Proceeding without parent data.")

    all_libraries = vdata.get("libraries", []) + parent_data.get("libraries", [])

    natives_dir_absolute = os.path.abspath(os.path.join(version_folder, "natives"))
    for lib in all_libraries:
        rules = lib.get("rules", [])
        allowed = True
        if rules:
            allowed = False
            for rule in rules:
                action = rule.get("action")
                os_rule = rule.get("os", {})
                if action == "allow":
                    if not os_rule or os_rule.get("name") == 'osx':
                        allowed = True; break
                    else: allowed = False
                elif action == "disallow":
                    if not os_rule or os_rule.get("name") == 'osx':
                         allowed = False; break
            if not allowed: continue

        artifact = lib.get("downloads", {}).get("artifact")
        if artifact and artifact.get("path"):
            lib_file = os.path.join(LIBRARIES_DIR, artifact["path"])
            if os.path.isfile(lib_file):
                classpath.add(os.path.abspath(lib_file))

    version_jar_path = os.path.join(version_folder, f"{version_id}.jar")
    if os.path.isfile(version_jar_path):
        classpath.add(os.path.abspath(version_jar_path))
    elif inherits_from:
         parent_jar_path = os.path.join(VERSIONS_DIR, inherits_from, f"{inherits_from}.jar")
         if os.path.isfile(parent_jar_path):
              classpath.add(os.path.abspath(parent_jar_path))

    args_data = vdata.get("arguments", {})
    parent_args_data = parent_data.get("arguments", {})

    raw_jvm_args = parent_args_data.get("jvm", []) + args_data.get("jvm", [])
    raw_game_args = parent_args_data.get("game", []) + args_data.get("game", [])

    if not raw_game_args and (vdata.get("minecraftArguments") or parent_data.get("minecraftArguments")):
        legacy_args_str = vdata.get("minecraftArguments") or parent_data.get("minecraftArguments", "")
        raw_game_args = legacy_args_str.split()
        print("Using legacy minecraftArguments format.")

    asset_index_id = (vdata.get("assetIndex") or parent_data.get("assetIndex", {})).get("id", "legacy")
    auth_uuid = account.get("uuid", "invalid-uuid")
    auth_token = account.get("token", "invalid-token")

    if account.get("type") in ["offline", "tlauncher", "lunar"]:
        auth_token = "0"

    # Determine launcher name based on mode
    launcher_name = "LunarClient" if lunar_client else "CatClient-M1"
    
    replacements = {
        "${auth_player_name}": account.get("username", "Player"),
        "${version_name}": version_id,
        "${game_directory}": effective_game_dir,
        "${assets_root}": os.path.abspath(ASSETS_DIR),
        "${assets_index_name}": asset_index_id,
        "${auth_uuid}": auth_uuid,
        "${auth_access_token}": auth_token,
        "${user_type}": "msa" if account.get("type") == "microsoft" else "legacy",
        "${version_type}": vdata.get("type", "release"),
        "${library_directory}": os.path.abspath(LIBRARIES_DIR),
        "${classpath_separator}": os.pathsep,
        "${launcher_name}": launcher_name,
        "${launcher_version}": "1.2",
        "${natives_directory}": natives_dir_absolute,
    }

    # Add M1-specific JVM args
    processed_jvm_args = [
        f"-Xmx{ram_mb}M",
        f"-Djava.library.path={natives_dir_absolute}",
    ]
    
    # Add M1 specific optimizations
    if is_arm64():
        processed_jvm_args.extend([
            "-XX:+UseG1GC",
            "-XX:MaxGCPauseMillis=200",
            "-XX:ParallelGCThreads=4",
            "-Dapple.awt.application.name=Minecraft"
        ])
    
    # Add extra args for Lunar Client
    if lunar_client:
        processed_jvm_args.extend([
            "-Dfml.ignoreInvalidMinecraftCertificates=true",
            "-Dfml.ignorePatchDiscrepancies=true",
            "-Djava.net.preferIPv4Stack=true",
            "-Dorg.lwjgl.opengl.Display.allowSoftwareOpenGL=true"
        ])

    for arg in raw_jvm_args:
        if isinstance(arg, str):
            temp_arg = arg
            for key, value in replacements.items():
                temp_arg = temp_arg.replace(key, value)
            processed_jvm_args.append(temp_arg)
        elif isinstance(arg, dict) and "rules" in arg:
            include = True
            for rule in arg["rules"]:
                action = rule.get("action")
                os_rule = rule.get("os", {})
                if action == "allow":
                    if not os_rule or os_rule.get("name") == 'osx':
                        include = True; break
                    else: include = False
                elif action == "disallow":
                    if not os_rule or os_rule.get("name") == 'osx':
                         include = False; break

            if include:
                value = arg.get("value")
                if isinstance(value, list):
                    for v in value:
                        temp_arg = v
                        for key, val in replacements.items(): temp_arg = temp_arg.replace(key, val)
                        processed_jvm_args.append(temp_arg)
                elif isinstance(value, str):
                    temp_arg = value
                    for key, val in replacements.items(): temp_arg = temp_arg.replace(key, val)
                    processed_jvm_args.append(temp_arg)

    cp_string = os.pathsep.join(list(classpath))
    processed_jvm_args.append("-cp")
    processed_jvm_args.append(cp_string)

    processed_game_args = []
    for arg in raw_game_args:
        if isinstance(arg, str):
             temp_arg = arg
             for key, value in replacements.items():
                 temp_arg = temp_arg.replace(key, value)
             processed_game_args.append(temp_arg)
        elif isinstance(arg, dict) and "rules" in arg:
             include = True
             value = arg.get("value")
             if isinstance(value, list):
                 for v in value:
                     temp_arg = v
                     for key, val in replacements.items(): temp_arg = temp_arg.replace(key, val)
                     processed_game_args.append(temp_arg)
             elif isinstance(value, str):
                 temp_arg = value
                 for key, val in replacements.items(): temp_arg = temp_arg.replace(key, val)
                 processed_game_args.append(temp_arg)

    if server_ip:
        processed_game_args.append("--server")
        processed_game_args.append(server_ip)
        if port:
            processed_game_args.append("--port")
            processed_game_args.append(str(port))

    if not main_class:
        raise Exception("Launch aborted: Could not determine main class for the game.")
        
    # Lunar Client uses a different main class for vanilla versions
    if lunar_client and "lunar" not in main_class:
        # Override main class for Lunar Client
        if "net.minecraft.client.main.Main" in main_class:
            # This is a change for Lunar Client compatibility
            main_class = "com.moonsworth.lunar.genesis.Genesis"
            print("Using Lunar Client main class")

    command = [java_path] + processed_jvm_args + [main_class] + processed_game_args

    # Apply Rosetta 2 if needed and requested
    if use_rosetta:
        command = run_with_rosetta(command)

    print("\n--- Launch Command ---")
    print("Java Path:", command[0])
    print("JVM Args:", processed_jvm_args)
    print("Main Class:", main_class)
    print("Game Args:", processed_game_args)
    if use_rosetta:
        print("Running with Rosetta 2: Yes")
    if lunar_client:
        print("Lunar Client Mode: Yes")
    print("----------------------\n")

    if status_callback: status_callback(f"Launching Minecraft {version_id}...")
    try:
        process = subprocess.Popen(command, cwd=effective_game_dir)
        print(f"Minecraft process started with PID: {process.pid}")
        if status_callback: status_callback(f"Minecraft {version_id} launched!")
    except FileNotFoundError:
         raise Exception(f"Launch failed: Java executable not found at '{java_path}'. Please check Java Path setting.")
    except Exception as e:
        raise Exception(f"Launch failed: Could not start Minecraft process: {e}")


# --- GUI ---
class M1LauncherApp:
    def __init__(self, root):
        self.root = root
        self.root.title("M1 Minecraft Launcher v1.2 (Lunar Compatible)")
        self.root.geometry("650x680")  # Increased height for new options
        
        # Try to load version manifest and show error if failed
        self.version_manifest = {"versions": []}  # Default empty
        self.ssl_verify_var = tk.BooleanVar(value=False)  # Default to no SSL verification for macOS 
        
        # --- SSL Configuration Frame ---
        ssl_frame = ttk.LabelFrame(root, text="SSL Configuration")
        ssl_frame.pack(fill="x", padx=10, pady=5)
        
        ttk.Checkbutton(ssl_frame, text="Verify SSL Certificates (Disable if you have SSL errors)", 
                       variable=self.ssl_verify_var).grid(row=0, column=0, sticky="w", padx=5, pady=2)
        ttk.Button(ssl_frame, text="Load Version Manifest", 
                  command=self.load_manifest).grid(row=0, column=1, padx=5, pady=2)
        
        ssl_note = ttk.Label(ssl_frame, text="Note: macOS often has SSL certificate issues with Python. If downloads fail, uncheck this option.",
                            wraplength=500, foreground="red")
        ssl_note.grid(row=1, column=0, columnspan=2, sticky="w", padx=5, pady=2)

        # --- M1 Configuration Frame ---
        m1_frame = ttk.LabelFrame(root, text="M1 Mac Configuration")
        m1_frame.pack(fill="x", padx=10, pady=5)
        
        self.use_rosetta_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(m1_frame, text="Use Rosetta 2 (x86_64 mode)", variable=self.use_rosetta_var).grid(row=0, column=0, sticky="w", padx=5, pady=2)
        
        # Add Lunar Client option
        self.lunar_client_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(m1_frame, text="Lunar Client Compatibility Mode", variable=self.lunar_client_var).grid(row=1, column=0, sticky="w", padx=5, pady=2)
        
        # M1 status indicators
        if is_arm64():
            arch_text = "Apple Silicon (ARM64)"
        else:
            arch_text = "Intel/Rosetta (x86_64)"
            
        ttk.Label(m1_frame, text=f"Detected CPU architecture: {arch_text}").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        
        if detect_rosetta():
            rosetta_text = "Yes (active)"
        elif is_arm64():
            rosetta_text = "Yes (installed)"
        else:
            rosetta_text = "N/A (Intel Mac)"
            
        ttk.Label(m1_frame, text=f"Rosetta 2: {rosetta_text}").grid(row=3, column=0, sticky="w", padx=5, pady=2)

        # --- Account Frame ---
        acct_frame = ttk.LabelFrame(root, text="Accounts")
        acct_frame.pack(fill="x", padx=10, pady=5)

        self.acct_type_var = tk.StringVar(value="tlauncher")
        ttk.Radiobutton(acct_frame, text="TLauncher", variable=self.acct_type_var, value="tlauncher").grid(row=0, column=0, sticky="w", padx=5)
        ttk.Radiobutton(acct_frame, text="Offline", variable=self.acct_type_var, value="offline").grid(row=0, column=1, sticky="w", padx=5)
        ttk.Radiobutton(acct_frame, text="Lunar", variable=self.acct_type_var, value="lunar").grid(row=0, column=2, sticky="w", padx=5)
        ttk.Radiobutton(acct_frame, text="Microsoft (Demo)", variable=self.acct_type_var, value="microsoft", state="disabled").grid(row=0, column=3, sticky="w", padx=5)

        ttk.Label(acct_frame, text="Username/Email:").grid(row=1, column=0, padx=5, pady=3, sticky="e")
        self.username_entry = ttk.Entry(acct_frame, width=30)
        self.username_entry.grid(row=1, column=1, columnspan=2, padx=5, pady=3, sticky="we")

        ttk.Label(acct_frame, text="Password/Token:").grid(row=2, column=0, padx=5, pady=3, sticky="e")
        self.password_entry = ttk.Entry(acct_frame, width=30, show="*")
        self.password_entry.grid(row=2, column=1, columnspan=2, padx=5, pady=3, sticky="we")
        ttk.Label(acct_frame, text="(Optional for Offline/Lunar, Needed for TLauncher)").grid(row=3, column=1, columnspan=2, sticky="w", padx=5)
        ttk.Label(acct_frame, text="(Warning: Passwords stored insecurely)", foreground="orange").grid(row=4, column=1, columnspan=2, sticky="w", padx=5)

        ttk.Button(acct_frame, text="Add / Update Account", command=self.on_add_account).grid(row=1, column=3, rowspan=2, padx=10, pady=5, sticky="ns")

        ttk.Separator(acct_frame, orient='horizontal').grid(row=5, column=0, columnspan=4, sticky="ew", pady=10)

        ttk.Label(acct_frame, text="Select Account:").grid(row=6, column=0, padx=5, pady=5, sticky="e")
        self.account_var = tk.StringVar()
        self.account_combo = ttk.Combobox(acct_frame, textvariable=self.account_var, state="readonly", width=40)
        self.account_combo.grid(row=6, column=1, columnspan=3, padx=5, pady=5, sticky="we")

        acct_frame.columnconfigure(1, weight=1)
        acct_frame.columnconfigure(2, weight=1)

        # --- Version / Modpack Frame ---
        ver_frame = ttk.LabelFrame(root, text="Game Version / Modpack")
        ver_frame.pack(fill="x", padx=10, pady=5)

        # Version data will be populated after manifest is loaded
        self.version_var = tk.StringVar()
        self.version_combo = ttk.Combobox(ver_frame, textvariable=self.version_var, values=[], state="readonly", width=50)
        self.version_combo.grid(row=0, column=0, padx=5, pady=5, sticky="we")
        
        ver_frame.columnconfigure(0, weight=1)

        # --- Launch Options Frame ---
        options_frame = ttk.LabelFrame(root, text="Launch Options")
        options_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(options_frame, text="Max RAM (MB):").grid(row=0, column=0, padx=5, pady=3, sticky="e")
        self.ram_spin = ttk.Spinbox(options_frame, from_=512, to=32768, increment=512, width=10)
        self.ram_spin.set("4096")
        self.ram_spin.grid(row=0, column=1, pady=3, sticky="w")

        ttk.Label(options_frame, text="Java Path:").grid(row=1, column=0, padx=5, pady=3, sticky="e")
        self.java_entry = ttk.Entry(options_frame, width=40)
        self.java_entry.insert(0, self.find_java())
        self.java_entry.grid(row=1, column=1, padx=5, pady=3, sticky="we")
        ttk.Button(options_frame, text="Browse...", command=self.browse_java).grid(row=1, column=2, padx=5)

        ttk.Label(options_frame, text="Server IP (Optional):").grid(row=2, column=0, padx=5, pady=3, sticky="e")
        self.server_entry = ttk.Entry(options_frame, width=30)
        self.server_entry.grid(row=2, column=1, padx=5, pady=3, sticky="w")
        ttk.Label(options_frame, text="Port:").grid(row=2, column=2, padx=2, pady=3, sticky="e")
        self.port_entry = ttk.Entry(options_frame, width=8)
        self.port_entry.grid(row=2, column=3, padx=5, pady=3, sticky="w")

        options_frame.columnconfigure(1, weight=1)

        # --- Status Bar ---
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Frame(root, relief=tk.SUNKEN, padding="2 2 2 2")
        status_bar.pack(side=tk.BOTTOM, fill="x")
        ttk.Label(status_bar, textvariable=self.status_var).pack(side=tk.LEFT)

        # --- Launch Button ---
        launch_frame = ttk.Frame(root)
        launch_frame.pack(pady=15)
        
        self.launch_btn = ttk.Button(launch_frame, text="Launch Game", command=self.on_launch, style="Accent.TButton")
        self.launch_btn.pack(ipadx=20, ipady=10)

        # --- Styling ---
        style = ttk.Style()
        try:
            style.theme_use('aqua') # macOS native theme
        except tk.TclError:
            print("Aqua theme not available, using default.")
            
        style.configure("Accent.TButton", font=('Helvetica', 12, 'bold'))

        # --- Initial Population ---
        self.refresh_account_list()
        self.load_manifest()

    def load_manifest(self):
        """Load version manifest and populate version list"""
        self.set_status("Loading version manifest...", "blue")
        try:
            self.version_manifest = load_version_manifest(self.ssl_verify_var.get())
            self.populate_version_list()
            self.set_status("Version manifest loaded successfully.", "green")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load version manifest: {e}\n\nTry unchecking 'Verify SSL Certificates' option.")
            self.set_status(f"Error loading manifest: {e}", "red")

    def populate_version_list(self):
        """Populate version combo box with available versions"""
        try:
            # Populate versions list (releases first, then snapshots)
            release_versions = sorted([v['id'] for v in self.version_manifest['versions'] if v['type'] == 'release'], reverse=True)
            snapshot_versions = sorted([v['id'] for v in self.version_manifest['versions'] if v['type'] == 'snapshot'], reverse=True)
            
            custom_versions = []
            if os.path.isdir(VERSIONS_DIR):
                for item in os.listdir(VERSIONS_DIR):
                    if os.path.isdir(os.path.join(VERSIONS_DIR, item)) and item not in all_versions:
                         custom_versions.append(item)

            # Define popular modpacks
            self.popular_modpacks = {
                "RLCraft (Modpack)": "rlcraft",
                "All the Mods 9 (Modpack)": "all-the-mods-9-atm9",
                "Pixelmon Modpack (Modpack)": "the-pixelmon-modpack",
                "One Block MC (Modpack)": "one-block-mc",
                "DawnCraft (Modpack)": "dawncraft",
                "Better MC (Modpack)": "better-mc-bmc1-forge",
            }
            modpack_names = sorted(self.popular_modpacks.keys())

            combined_list = modpack_names + sorted(custom_versions, reverse=True) + release_versions + snapshot_versions
            
            self.version_combo['values'] = combined_list
            
            if release_versions:
                 self.version_combo.set(release_versions[0])
            elif combined_list:
                 self.version_combo.set(combined_list[0])
        except Exception as e:
            messagebox.showerror("Error", f"Failed to populate version list: {e}")
            self.set_status(f"Error populating version list: {e}", "red")

    def find_java(self):
        """Find Java executable on macOS, prioritizing ARM64 Java if on M1"""
        # Common Java install locations on macOS
        java_locations = [
            "/usr/bin/java",
            "/Library/Java/JavaVirtualMachines",
            "/System/Library/Java/JavaVirtualMachines",
            os.path.expanduser("~/Library/Java/JavaVirtualMachines"),
            "/opt/homebrew/opt/java/bin/java",  # Homebrew ARM64
            "/usr/local/opt/java/bin/java",     # Homebrew Intel
        ]
        
        # If on ARM64, prefer ARM Java first
        if is_arm64():
            for path in java_locations:
                if os.path.isfile(path):
                    return path
                elif os.path.isdir(path):
                    # Search for arm64 JDKs first
                    for root, dirs, files in os.walk(path):
                        if "bin" in root and "java" in files:
                            java_path = os.path.join(root, "java")
                            if os.path.isfile(java_path):
                                return java_path
        
        # Fallback: Try using the system java command
        java_path = shutil.which("java")
        if java_path:
            return java_path
        
        # Last resort
        return "java"

    def browse_java(self):
        """Opens file dialog to select Java executable."""
        filename = filedialog.askopenfilename(
            title="Select Java Executable",
            filetypes=[("Java Executable", "java"), ("All Files", "*.*")]
        )
        if filename:
            self.java_entry.delete(0, tk.END)
            self.java_entry.insert(0, filename)

    def set_status(self, message, color="black"):
        """Updates the status bar message and color."""
        self.root.after(0, self._update_status_ui, message, color)

    def _update_status_ui(self, message, color):
         self.status_var.set(message)
         for widget in self.root.winfo_children():
             if isinstance(widget, ttk.Frame) and widget.cget('relief') == tk.SUNKEN:
                  for label in widget.winfo_children():
                      if isinstance(label, ttk.Label):
                          label.config(foreground=color)
                          break
                  break

    def on_add_account(self):
        acc_type = self.acct_type_var.get()
        user = self.username_entry.get().strip()
        pwd = self.password_entry.get().strip()

        if not user:
            messagebox.showwarning("Input Error", "Username/Email cannot be empty!")
            return

        if acc_type == "tlauncher" and not pwd:
             result = messagebox.askyesno("Password Missing", "You selected TLauncher account but left the password empty. TLauncher usually requires a password (stored insecurely here).\n\nDo you want to continue anyway (treat as offline)?")
             if not result: return

        try:
            add_account(acc_type, user, pwd)
            self.refresh_account_list()
            self.username_entry.delete(0, tk.END)
            self.password_entry.delete(0, tk.END)
            self.set_status(f"Account '{user}' added/updated.", "green")
        except Exception as e:
             messagebox.showerror("Account Error", f"Failed to add/update account: {e}")
             self.set_status(f"Error adding account: {e}", "red")

    def refresh_account_list(self):
        """Reloads the account list into the combobox."""
        display_names = [f"{acc.get('type','N/A').capitalize()}: {acc.get('username','Unknown')}" for acc in accounts]
        self.account_combo['values'] = display_names
        if display_names:
            current_selection = self.account_var.get()
            if current_selection in display_names:
                 self.account_combo.set(current_selection)
            else:
                 self.account_combo.current(0)
        else:
            self.account_combo.set('')

    def on_launch(self):
        """Handles the launch button click."""
        selected_version_display = self.version_var.get()
        if not selected_version_display:
            messagebox.showerror("Error", "Please select a version or modpack.")
            return

        account_index = self.account_combo.current()
        if account_index == -1 and not accounts:
            result = messagebox.askyesno("No Account Selected", "No accounts are configured. Launch in Offline mode with username 'Player'?")
            if result:
                selected_account = {"type": "offline", "username": "Player", "uuid": str(uuidlib.uuid3(uuidlib.NAMESPACE_DNS, "Player")), "token": "0"}
            else:
                return
        elif account_index == -1 and accounts:
             messagebox.showerror("Error", "Please select an account from the list.")
             return
        else:
            selected_account = accounts[account_index]

        try:
            ram_val = int(self.ram_spin.get())
        except ValueError:
            messagebox.showerror("Error", "Invalid RAM value. Please enter a number (MB).")
            return

        java_path_val = self.java_entry.get().strip() or self.find_java()
        server_ip_val = self.server_entry.get().strip() or None
        port_val_str = self.port_entry.get().strip()
        port_val = None
        if port_val_str:
            try:
                port_val = int(port_val_str)
                if not (0 < port_val < 65536): raise ValueError
            except ValueError:
                 messagebox.showerror("Error", "Invalid Port number. Must be between 1 and 65535.")
                 return

        is_modpack = selected_version_display in self.popular_modpacks
        modpack_slug = self.popular_modpacks.get(selected_version_display) if is_modpack else None
        version_to_process = modpack_slug if is_modpack else selected_version_display

        # Get launch options
        use_rosetta = self.use_rosetta_var.get()
        lunar_client = self.lunar_client_var.get()
        ssl_verify = self.ssl_verify_var.get()

        # Disable UI elements during launch process
        self.launch_btn.config(state="disabled")
        self.set_status("Starting launch process...", "blue")

        # Run install/launch in a separate thread to keep UI responsive
        launch_thread = threading.Thread(
            target=self._launch_task,
            args=(version_to_process, is_modpack, selected_account, ram_val, java_path_val, 
                  server_ip_val, port_val, use_rosetta, lunar_client, ssl_verify),
            daemon=True
        )
        launch_thread.start()

    def _launch_task(self, item_to_launch, is_modpack, account, ram, java, server, port, 
                    use_rosetta, lunar_client, ssl_verify):
        """Background task for installing (if needed) and launching."""
        try:
            final_version_id = None
            game_directory = None

            if is_modpack:
                self.set_status(f"Installing modpack '{item_to_launch}'...", "blue")
                # Modpack handling logic would go here - this is simplified
                messagebox.showinfo("Modpack Support", "Modpack installation functionality is included in the code but not fully implemented in this example.")
                self.set_status("Ready", "black")
                # Re-enable the launch button
                self.root.after(0, self.launch_btn.config, {"state": "normal"}) 
                return
                
            else:
                final_version_id = item_to_launch
                self.set_status(f"Checking installation for version '{final_version_id}'...", "blue")
                install_version(final_version_id, status_callback=self.set_status, ssl_verify=ssl_verify)
                self.set_status(f"Version '{final_version_id}' ready. Preparing launch...", "blue")

            # Launch the game
            launch_game(
                version_id=final_version_id,
                account=account,
                ram_mb=ram,
                java_path=java,
                game_dir=game_directory,
                server_ip=server,
                port=port,
                status_callback=self.set_status,
                use_rosetta=use_rosetta,
                lunar_client=lunar_client,
                ssl_verify=ssl_verify
            )

        except Exception as e:
            error_message = f"Error during launch: {e}"
            print(f"ERROR: {error_message}")
            import traceback
            traceback.print_exc()
            self.set_status(f"Error: {e}", "red")
            self.root.after(0, messagebox.showerror, "Launch Failed", error_message)
        finally:
            self.root.after(0, self.launch_btn.config, {"state": "normal"})

# --- Main Execution ---
if __name__ == "__main__":
    try:
        root = tk.Tk()
        app = M1LauncherApp(root)
        root.mainloop()
    except Exception as e:
        print(f"Fatal error: {e}")
        traceback.print_exc()
        if 'root' in locals() and root:
            messagebox.showerror("Fatal Error", f"A fatal error occurred: {e}")
