' rewards_daily.vbs - run rewards automation silently (portable, self-healing)
' NOTE: keep this file pure ASCII - VBScript misreads UTF-8 bytes under GBK codepage
'
' Resolution order:
'   1) pyw.exe -3 tools\bootstrap.py   -> self-heals .venv, then runs the script
'      (survives system Python upgrades: 2026-11-02 Python 3.14 -> 3.15 killed the venv)
'   2) %BING_REWARDS_PYTHON%           -> explicit interpreter override
'   3) pythonw.exe on PATH             -> last resort
' History: until 2026-10-08 this pointed at
'   %USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe
' which WorkBuddy re-pointed to its bundled vendor python and wiped.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
DQ = Chr(34)
boot = base & "\tools\bootstrap.py"
pyw = sh.ExpandEnvironmentStrings("%SystemRoot%") & "\pyw.exe"
If fso.FileExists(pyw) And fso.FileExists(boot) Then
  cmd = DQ & pyw & DQ & " -3 " & DQ & boot & DQ
Else
  py = sh.ExpandEnvironmentStrings("%BING_REWARDS_PYTHON%")
  If InStr(py, "%BING_REWARDS_PYTHON%") > 0 Then py = ""
  If py = "" Then py = "pythonw.exe"
  cmd = DQ & py & DQ & " " & DQ & base & "\rewards_daily.py" & DQ
End If
sh.Run cmd, 0, True
