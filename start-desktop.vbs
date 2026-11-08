Option Explicit
Dim shell, files, root, packaged, electron, command
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
packaged = files.BuildPath(root, "desktop\dist\VEIL-win32-x64\VEIL.exe")
electron = files.BuildPath(root, "desktop\node_modules\electron\dist\electron.exe")
shell.CurrentDirectory = files.BuildPath(root, "desktop")
If files.FileExists(packaged) Then
  command = Chr(34) & packaged & Chr(34)
ElseIf files.FileExists(electron) Then
  command = Chr(34) & electron & Chr(34) & " ."
Else
  MsgBox "Please run setup-desktop.bat first.", 16, "VEIL"
  WScript.Quit 1
End If
shell.Run command, 0, False
