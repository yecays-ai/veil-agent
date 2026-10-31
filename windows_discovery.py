"""Universal Windows installed application discovery and security classification."""
import ntpath
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

# Shells, command interpreters, and script hosts that MUST NOT be launched
DISALLOWED_EXECUTABLES = frozenset({
    'cmd.exe', 'powershell.exe', 'pwsh.exe', 'wscript.exe', 'cscript.exe',
    'mshta.exe', 'rundll32.exe', 'regsvr32.exe', 'certutil.exe', 'bash.exe',
    'wsl.exe', 'wslconfig.exe', 'conhost.exe', 'wt.exe', 'python.exe',
    'pythonw.exe', 'py.exe', 'pymanager.exe', 'pyw.exe', 'pywmanager.exe',
    'python3.exe', 'node.exe', 'deno.exe', 'git-bash.exe', 'regedit.exe', 'mmc.exe',
    'cleanmgr.exe', 'dism.exe', 'taskmgr.exe', 'systemreset.exe', 'control.exe',
    'resmon.exe', 'perfmon.exe', 'eventvwr.exe', 'msconfig.exe', 'logonui.exe',
    'consent.exe', 'credwiz.exe', 'credentialuibroker.exe', 'lockapp.exe',
    'winlogon.exe', 'schtasks.exe', 'bitsadmin.exe', 'vssadmin.exe',
    'takeown.exe', 'icacls.exe', 'net.exe', 'net1.exe', 'ftp.exe', 'ssh.exe',
})

# Versioned interpreters and WSL distribution execution aliases
VERSIONED_SHELL_PATTERN = re.compile(
    r'^(?:python\d*|pythonw\d*|py\d*|wsl.*|ubuntu.*|debian.*|kali.*|arch.*|opensuse.*|sles.*)\.exe$',
    re.IGNORECASE,
)

# Web browsers requiring dedicated Guest isolation (Ticket 151)
BROWSER_PROCESSES = frozenset({
    'chrome.exe', 'msedge.exe', 'firefox.exe', 'brave.exe', 'opera.exe',
    'vivaldi.exe', 'waterfox.exe',
})

# Name heuristics for installers, uninstallers, helpers, and administrative utilities
DISALLOWED_NAME_PATTERNS = re.compile(
    r'(?:uninstall|unins\d*|卸载|setup|installer|安装程序|cmd|powershell|'
    r'terminal|终端|registry editor|注册表|management console|控制台|'
    r'administrative tools|管理工具|diagnostics|crashpad|feedback|updater|'
    r'auto[\s_-]*update|patcher|repair|service|host)',
    re.IGNORECASE,
)

# Dangerous characters or patterns in shortcut arguments
DANGEROUS_ARGUMENTS_PATTERN = re.compile(
    r'[;&|><`\$\n\r]'
    r'|\.(?:bat|cmd|vbs|ps1|sh|js|exe|scr|msi|reg)\b'
    r'|(?:-enc|-encodedcommand|-command|/c|/k|-c|-e)\b',
    re.IGNORECASE,
)

# Untrusted locations (user temp, downloads, desktop scratch files)
UNTRUSTED_PATH_PATTERN = re.compile(
    r'[\\/](?:temp|tmp|downloads|desktop|documents|recent|appdata[\\/]local[\\/]temp)\b',
    re.IGNORECASE,
)

# File dialog indicators: window titles or controls
FILE_DIALOG_TITLE_PATTERN = re.compile(
    r'(?:#32770|save as|open file|\bopen\b|另存为|打开文件|\b打开\b|选择文件|export\b|import\b|导出|导入|browse\b|浏览)',
    re.IGNORECASE,
)
FILE_DIALOG_CONTROL_PATTERN = re.compile(
    r'(?:filedialog|fileopendialog|filesavedialog|save as|open file|\bopen\b|另存为|打开文件|\b打开\b|浏览\b|browse\b|选择文件|export\b|import\b)',
    re.IGNORECASE,
)

# Generic confirmation labels that require context verification
GENERIC_CONFIRMATION_PATTERN = re.compile(
    r'^(?:ok|yes|确定|是|apply|应用|continue|继续)$',
    re.IGNORECASE,
)

# Text inputs that resemble paths, commands, or addresses
UNSAFE_TEXT_INPUT_PATTERN = re.compile(
    r'(?:path|file|cmd|command|url|address|location|query|search|filter|路径|文件|命令|地址|位置|搜索)',
    re.IGNORECASE,
)

# Process names known to handle sensitive credentials or security state
PROTECTED_PROCESSES = frozenset({
    'consent.exe', 'credentialuibroker.exe', 'credwiz.exe', 'logonui.exe',
    'lockapp.exe', 'winlogon.exe', '1password.exe', '1passwordcom.exe',
    'bitwarden.exe', 'keepass.exe', 'keepassxc.exe', 'lastpass.exe',
    'dashlane.exe', 'protonpass.exe', 'enpass.exe', 'roboform.exe',
})


def _slugify(text: str, fallback_exe: str = '') -> str:
    """Generate a clean lowercase ASCII identifier matching ^[a-z][a-z0-9_-]{0,31}$."""
    slug = re.sub(r'[^a-zA-Z0-9_-]+', '_', text.strip()).strip('_').lower()
    if not slug or not slug[0].isalpha() or slug in ('app', 'app_'):
        if fallback_exe:
            clean_exe = os.path.splitext(ntpath.basename(fallback_exe))[0]
            fallback_slug = re.sub(r'[^a-zA-Z0-9_-]+', '_', clean_exe.strip()).strip('_').lower()
            if fallback_slug and fallback_slug[0].isalpha():
                slug = fallback_slug
    if not slug or not slug[0].isalpha() or slug in ('app', 'app_'):
        slug = f'app_{slug}'.rstrip('_')
    if not slug or not slug[0].isalpha() or slug in ('app', 'app_'):
        slug = 'app_custom'
    return slug[:30].rstrip('_')


class WindowsAppDiscoveryBackend:
    """System discovery backend using Start Menu, App Paths, and WindowsApps aliases."""

    def get_start_menu_dirs(self) -> List[str]:
        if os.name != 'nt':
            return []
        dirs = [
            os.path.expandvars(r'%APPDATA%\Microsoft\Windows\Start Menu\Programs'),
            os.path.expandvars(r'%ALLUSERSPROFILE%\Microsoft\Windows\Start Menu\Programs'),
        ]
        return [d for d in dirs if os.path.isdir(d)]

    def get_windows_apps_dir(self) -> Optional[str]:
        if os.name != 'nt':
            return None
        path = os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\WindowsApps')
        return path if os.path.isdir(path) else None

    def get_system_apps(self) -> List[Tuple[str, str]]:
        if os.name != 'nt':
            return []
        sysroot = os.environ.get('SystemRoot', r'C:\Windows')
        system32 = os.path.join(sysroot, 'System32')
        candidates = [
            ('Calculator', os.path.join(system32, 'calc.exe')),
            ('Paint', os.path.join(system32, 'mspaint.exe')),
            ('WordPad', os.path.join(system32, 'write.exe')),
            ('Character Map', os.path.join(system32, 'charmap.exe')),
        ]
        return [(label, p) for label, p in candidates if os.path.isfile(p)]

    def read_shortcut(self, lnk_path: str) -> Tuple[str, str, str]:
        """Return (target_path, arguments, working_directory) from a .lnk shortcut."""
        if os.name != 'nt':
            return '', '', ''
        try:
            import win32com.client
            shell = win32com.client.Dispatch('WScript.Shell')
            shortcut = shell.CreateShortcut(lnk_path)
            return (
                str(shortcut.TargetPath or '').strip(),
                str(shortcut.Arguments or '').strip(),
                str(shortcut.WorkingDirectory or '').strip(),
            )
        except Exception:
            return '', '', ''

    def read_registry_app_paths(self) -> Dict[str, str]:
        """Read HKLM and HKCU App Paths registered applications."""
        if os.name != 'nt':
            return {}
        import winreg
        results = {}
        for root_key in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(root_key, r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths') as key:
                    num_subkeys = winreg.QueryInfoKey(key)[0]
                    for i in range(num_subkeys):
                        try:
                            subkey_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, subkey_name) as subkey:
                                val, _ = winreg.QueryValueEx(subkey, '')
                                if isinstance(val, str) and val.strip():
                                    clean_val = val.strip().strip('"')
                                    if clean_val.lower().endswith('.exe') and os.path.isfile(clean_val):
                                        results[subkey_name] = clean_val
                        except Exception:
                            continue
            except Exception:
                continue
        return results

    def file_exists(self, path: str) -> bool:
        return bool(path and os.path.isfile(path))


class WindowsAppDiscovery:
    """Scans and validates Windows desktop applications into safe, tiered capability records."""

    def __init__(self, backend: Optional[WindowsAppDiscoveryBackend] = None):
        self.backend = backend or WindowsAppDiscoveryBackend()

    def discover_raw_candidates(self) -> List[Dict[str, Any]]:
        """Collect raw candidates from Start Menu, WindowsApps, App Paths, and System32."""
        candidates: List[Dict[str, Any]] = []

        # 1. Start Menu Shortcuts (.lnk)
        for sm_dir in self.backend.get_start_menu_dirs():
            try:
                for root, _, files in os.walk(sm_dir):
                    for file in files:
                        if file.lower().endswith('.lnk'):
                            lnk_path = os.path.join(root, file)
                            name = os.path.splitext(file)[0].strip()
                            target, args, cwd = self.backend.read_shortcut(lnk_path)
                            if target:
                                candidates.append({
                                    'name': name,
                                    'target': target,
                                    'args': args,
                                    'source': 'start_menu',
                                    'shortcut_path': lnk_path,
                                    'cwd': cwd,
                                })
            except Exception:
                continue

        # 2. WindowsApps Execution Aliases
        win_apps_dir = self.backend.get_windows_apps_dir()
        if win_apps_dir and os.path.isdir(win_apps_dir):
            try:
                for entry in os.listdir(win_apps_dir):
                    if entry.lower().endswith('.exe'):
                        full_path = os.path.join(win_apps_dir, entry)
                        stem = os.path.splitext(entry)[0]
                        candidates.append({
                            'name': stem,
                            'target': full_path,
                            'args': '',
                            'source': 'windows_apps',
                        })
            except Exception:
                pass

        # 3. Registry App Paths
        reg_apps = self.backend.read_registry_app_paths()
        for subkey_name, target in reg_apps.items():
            stem = os.path.splitext(subkey_name)[0]
            candidates.append({
                'name': stem,
                'target': target,
                'args': '',
                'source': 'registry_app_paths',
            })

        # 4. Known System Utilities
        for label, path in self.backend.get_system_apps():
            candidates.append({
                'name': label,
                'target': path,
                'args': '',
                'source': 'system32',
            })

        return candidates

    def validate_candidate(self, candidate: Dict[str, Any]) -> Tuple[bool, Optional[str], Dict[str, bool]]:
        """Validate whether a candidate application is safe to launch, observe, or interact with.

        Returns: (is_safe, refusal_reason, capabilities)
        """
        target = candidate.get('target', '').strip()
        name = candidate.get('name', '').strip()
        args = candidate.get('args', '').strip()

        capabilities = {
            'can_launch': False,
            'can_observe': False,
            'can_interact': False,
        }

        # 1. Target must be a non-empty string ending with .exe
        if not target or not target.lower().endswith('.exe'):
            return False, 'Target is not an executable file.', capabilities

        # 2. Target must exist on disk (unless running mocked/non-nt environment where bypassed)
        if not self.backend.file_exists(target):
            return False, 'Executable does not exist on disk.', capabilities

        base_exe = ntpath.basename(target).lower()

        # 3. Protected security / credential / logon processes
        if base_exe in PROTECTED_PROCESSES:
            return False, 'Target is a protected credential or security process.', capabilities

        # 4. Command shells, script interpreters, and administrative elevation binaries
        if base_exe in DISALLOWED_EXECUTABLES or VERSIONED_SHELL_PATTERN.match(base_exe):
            return False, f'Target executable {base_exe} is a restricted system binary, interpreter, or shell.', capabilities

        # 5. Browsers require dedicated Guest profile isolation
        if base_exe in BROWSER_PROCESSES:
            return False, f'Browser executable {base_exe} requires dedicated Guest isolation.', capabilities

        # 6. Name heuristics for installers, uninstallers, helpers
        if DISALLOWED_NAME_PATTERNS.search(name) or DISALLOWED_NAME_PATTERNS.search(base_exe):
            return False, f'Application name or executable indicates an uninstaller or installer: {name}', capabilities

        # 7. Target path heuristics (e.g. unins000.exe, temp directory setups)
        norm_target = ntpath.normcase(ntpath.normpath(target))
        if re.search(r'[\\/](?:unins\d*|setup|installer)[^\\/]*\.exe$', norm_target):
            return False, 'Target path belongs to an installer or uninstaller.', capabilities

        # 8. Untrusted locations (Downloads, Temp, Desktop, Documents scratch)
        if UNTRUSTED_PATH_PATTERN.search(norm_target):
            return False, 'Target executable is in an untrusted or user-data location.', capabilities

        # 9. Dangerous arguments
        if args and DANGEROUS_ARGUMENTS_PATTERN.search(args):
            return False, 'Target shortcut contains dangerous or dynamic arguments.', capabilities

        # If it passed all safety checks:
        capabilities['can_launch'] = True
        capabilities['can_observe'] = True
        capabilities['can_interact'] = True

        return True, None, capabilities

    def discover_applications(self, existing_apps: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Discover, deduplicate, validate, and build application records."""
        raw_candidates = self.discover_raw_candidates()
        seen_targets: Set[str] = set()
        seen_ids: Set[str] = set(existing_apps.keys()) if existing_apps else set()

        # Collect configured static application targets to avoid emitting duplicates
        configured_targets = {
            ntpath.normcase(ntpath.normpath(app.get('launch_path', '')))
            for app in (existing_apps or {}).values()
            if isinstance(app, dict) and app.get('launch_path')
        }

        results: List[Dict[str, Any]] = []

        for cand in raw_candidates:
            target = cand.get('target', '').strip()
            norm_target = ntpath.normcase(ntpath.normpath(target))
            if norm_target in seen_targets or norm_target in configured_targets:
                continue

            is_safe, _reason, caps = self.validate_candidate(cand)
            if not is_safe:
                continue

            seen_targets.add(norm_target)

            raw_name = cand.get('name') or os.path.splitext(ntpath.basename(target))[0]
            clean_name = raw_name.strip()

            # Generate unique safe app_id
            base_slug = _slugify(clean_name, fallback_exe=target)
            app_id = base_slug
            counter = 2
            while app_id in seen_ids:
                app_id = f'{base_slug[:28]}_{counter}'
                counter += 1
            seen_ids.add(app_id)

            # Build process_paths matching patterns safely bound to application directory
            base_name = ntpath.basename(target).lower()
            process_paths = [norm_target]

            prog_files = ntpath.normcase(ntpath.normpath(os.environ.get('ProgramFiles', 'C:\\Program Files')))
            # Packaged store app execution paths
            if 'windowsapps' in norm_target:
                process_paths.append(f'{prog_files}\\windowsapps\\*\\{base_name}')
                if 'mspaint' in base_name or 'pbrush' in base_name:
                    process_paths.extend([f'{prog_files}\\windowsapps\\*\\paintapp.exe', norm_target])
            elif base_name == 'calc.exe':
                process_paths.extend([f'{prog_files}\\windowsapps\\*\\calculatorapp.exe', norm_target])
            elif base_name == 'mspaint.exe':
                process_paths.extend([f'{prog_files}\\windowsapps\\*\\paintapp.exe', norm_target])

            process_paths = [ntpath.normcase(ntpath.normpath(p)) for p in process_paths]

            app_record = {
                'app_id': app_id,
                'label': clean_name,
                'launch_path': target,
                'launch_args': cand.get('args') or None,
                'process_paths': tuple(process_paths),
                'actions': frozenset({
                    'activate', 'minimize', 'restore', 'invoke', 'select', 'toggle',
                    'expand', 'collapse', 'set_text',
                }),
                'discovered': True,
                'can_launch': caps['can_launch'],
                'can_observe': caps['can_observe'],
                'can_interact': caps['can_interact'],
                'public_web': False,
            }
            results.append(app_record)

        return results


def is_file_dialog_control(metadata: Dict[str, Any], window: Optional[Dict[str, Any]] = None) -> bool:
    """Check if a UIA control belongs to a file open/save dialog."""
    window_title = (window.get('title') or '') if window else ''
    window_class = (window.get('class_name') or '') if window else ''

    if window_class == '#32770' or FILE_DIALOG_TITLE_PATTERN.search(window_title):
        return True

    name = metadata.get('name') or ''
    auto_id = metadata.get('automation_id') or ''
    ancestors = ' '.join(metadata.get('ancestor_labels', ()))

    combined = f'{name} {auto_id} {ancestors}'
    return bool(FILE_DIALOG_CONTROL_PATTERN.search(combined))


def is_ambiguous_confirmation(metadata: Dict[str, Any], window: Optional[Dict[str, Any]] = None) -> bool:
    """Check if an action is a generic confirmation button in an unverified or dialog scope."""
    name = (metadata.get('name') or '').strip()
    if not GENERIC_CONFIRMATION_PATTERN.match(name):
        return False

    window_class = (window.get('class_name') or '') if window else ''
    window_title = (window.get('title') or '') if window else ''
    ancestors = metadata.get('ancestor_labels', ())

    # Win32 dialog class #32770 or dialog keywords
    if window_class == '#32770':
        return True

    # If the window title indicates a dialog or popup
    if re.search(r'(?i)(?:dialog|confirm|save|replace|delete|overwrite|warning|alert|确认|保存|替换|删除|覆盖|警告|提示|对话框)', window_title):
        return True

    # If ancestors contain dialog indications
    if any(re.search(r'(?i)(?:dialog|messagebox|confirm|warning|alert|提示|确认|警告|对话框)', label) for label in ancestors):
        return True

    # If not explicitly inside a verified toolbar or ribbon, reject generic confirmation
    parent_type = metadata.get('parent_type') or ''
    if parent_type in ('WindowControl', 'PaneControl', 'CustomControl') or not ancestors:
        return True

    return False


def is_unsafe_text_input(metadata: Dict[str, Any]) -> bool:
    """Check if a text edit control is intended for file paths, commands, or addresses."""
    name = metadata.get('name') or ''
    auto_id = metadata.get('automation_id') or ''
    ancestors = ' '.join(metadata.get('ancestor_labels', ()))
    combined = f'{name} {auto_id} {ancestors}'
    return bool(UNSAFE_TEXT_INPUT_PATTERN.search(combined))
