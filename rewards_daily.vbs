' rewards_daily.vbs - run rewards_daily.py silently (portable)
' NOTE: keep this file pure ASCII - VBScript misreads UTF-8 bytes under GBK codepage
' Resolution order:
'   1) project-local .venv pythonw  (stable; NOT wiped by WorkBuddy updates)
'   2) %BING_REWARDS_PYTHON%        (set by `python rewards_daily.py --setup`)
'   3) pythonw.exe on PATH          (standard Python installs provide it)
' History: until 2026-10-08 this pointed at
'   %USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe
' which WorkBuddy re-pointed to its bundled vendor python and wiped
' (requests / websocket-client gone) -> automation silently failed every day.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
venvpy = base & "\.venv\Scripts\pythonw.exe"
If fso.FileExists(venvpy) Then
  py = venvpy
Else
  py = sh.ExpandEnvironmentStrings("%BING_REWARDS_PYTHON%")
  If InStr(py, "%BING_REWARDS_PYTHON%") > 0 Then py = ""
  If py = "" Then py = "pythonw.exe"
End If
script = base & "\rewards_daily.py"
DQ = Chr(34)
cmd = DQ & py & DQ & " " & DQ & script & DQ
sh.Run cmd, 0, True
