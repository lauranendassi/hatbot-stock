Attribute VB_Name = "ModuleMode"
Option Explicit

Private Sub Workbook_SheetChange(ByVal Sh As Object, ByVal Target As Range)
    Dim pwd As String
    Dim mode As String
    pwd = "weloobe"
    
    Select Case Sh.Name
        Case "Ventes", "Achats", "Vue du jour", "Prix dynamiques"
            If Not Intersect(Target, Sh.Range("B1")) Is Nothing Then
                mode = Sh.Range("B1").Value
                
                ' Couleur selon le mode
                If mode = "Admin" Then
                    Sh.Range("B1").Interior.Color = RGB(192, 0, 0)
                Else
                    Sh.Range("B1").Interior.Color = RGB(84, 130, 53)
                    Sh.Range("B1").Value = "Utilisateur"
                    mode = "Utilisateur"
                End If
                
                ' Reappliquer la protection (l'utilisateur seul ne peut pas la lever)
                Sh.Unprotect Password:=pwd
                Sh.Protect Password:=pwd, UserInterfaceOnly:=True
                
                MsgBox "Mode " & mode & " active sur la feuille " & Sh.Name, vbInformation, "Mode"
            End If
    End Select
End Sub
