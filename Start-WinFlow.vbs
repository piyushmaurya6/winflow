Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = baseDir
pythonw = baseDir & "\.venv\Scripts\pythonw.exe"
script = baseDir & "\run_winflow.py"
shell.Run """" & pythonw & """ """ & script & """", 0, False
