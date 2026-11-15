# Backup trigger for the cloud pipeline, run by the Windows task "HollowFloor-kick" (GitHub cron is unreliable on new repos).
# Delete the task once scheduled runs fire on their own:  Unregister-ScheduledTask HollowFloor-kick
# gh can't read its own keyring entry from a scheduled task, so read it here (Windows Credential Manager) and pass it in memory.
Add-Type -Namespace HF -Name Cred -MemberDefinition @'
[DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern bool CredRead(string target, int type, int flags, out IntPtr cred);
[DllImport("advapi32.dll")] public static extern void CredFree(IntPtr cred);
[StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] public struct C { public int Flags; public int Type; public string Target; public string Comment; public long Written; public int BlobSize; public IntPtr Blob; public int Persist; public int AttrCount; public IntPtr Attrs; public string Alias; public string User; }
public static string Read(string target) { IntPtr p; if (!CredRead(target, 1, 0, out p)) return null; var c = (C)Marshal.PtrToStructure(p, typeof(C)); var b = new byte[c.BlobSize]; Marshal.Copy(c.Blob, b, 0, c.BlobSize); var s = System.Text.Encoding.UTF8.GetString(b); CredFree(p); return s; }
'@
foreach ($t in 'gh:github.com:yo2coo1', 'gh:github.com:') { $tok = [HF.Cred]::Read($t); if ($tok) { $env:GH_TOKEN = $tok.Trim([char]0); break } }
$log = Join-Path $PSScriptRoot '..\out\kick.log'
$out = & 'C:\Program Files\GitHub CLI\gh.exe' workflow run tick -R yo2coo1/hollow-floor -f dry_run=false -f scratch=false 2>&1
"$(Get-Date -Format s) exit=$LASTEXITCODE $out" | Add-Content $log
