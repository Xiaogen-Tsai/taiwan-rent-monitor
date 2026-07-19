Option Explicit

Dim shell
Dim fso
Dim scriptDir
Dim projectRoot
Dim psScript
Dim command

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
projectRoot = fso.GetParentFolderName(scriptDir)
psScript = fso.BuildPath(scriptDir, "run_watch.ps1")

shell.CurrentDirectory = projectRoot
command = "powershell.exe -NoLogo -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File " & Chr(34) & psScript & Chr(34)
shell.Run command, 0, True
