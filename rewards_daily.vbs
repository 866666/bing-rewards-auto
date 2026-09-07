' rewards_daily.vbs - run rewards_daily.py silently (portable)
' NOTE: keep this file pure ASCII - VBScript misreads UTF-8 bytes under GBK codepage
' Resolution order:
'   1) %BING_REWARDS_PYTHON%  (set by `python rewards_daily.py --setup`)
'   2) pythonw.exe on PATH  (standard Python installs provide it)
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
py = sh.ExpandEnvironmentStrings("%BING_REWARDS_PYTHON%")
If InStr(py, "%BING_REWARDS_PYTHON%") > 0 Then py = ""
If py = "" Then py = "pythonw.exe"
script = base & "\rewards_daily.py"
DQ = Chr(34)
cmd = DQ & py & DQ & " " & DQ & script & DQ
sh.Run cmd, 0, True