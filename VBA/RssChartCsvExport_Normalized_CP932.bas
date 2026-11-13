Attribute VB_Name = "RssChartCsvExport"
Option Explicit

Public Sub ExportRssChartCsv()
    Dim ws As Worksheet
    Dim outWb As Workbook
    Dim outWs As Worksheet

    Dim headerRow As Long
    Dim firstDataRow As Long
    Dim lastRow As Long
    Dim r As Long
    Dim outRow As Long

    Dim symbolCode As String
    Dim targetLabel As String
    Dim securityName As String
    Dim marketName As String
    Dim timeframeCode As String
    Dim timeframeLabel As String
    Dim bars As String

    Dim basePath As String
    Dim fileName As String
    Dim fullPath As String
    Dim n As Long

    Dim oldAlerts As Boolean
    Dim oldScreenUpdating As Boolean

    On Error GoTo ErrHandler

    Set ws = ThisWorkbook.Worksheets("CHART")

    ' RssChartの見出し行を探す（通常は13行目付近）。
    headerRow = FindRssHeaderRow(ws)
    If headerRow = 0 Then
        MsgBox "RssChart の見出し行を確認できませんでした。データ取得後に実行してください。", vbExclamation
        Exit Sub
    End If

    firstDataRow = headerRow + 1
    lastRow = ws.Cells(ws.Rows.Count, 4).End(xlUp).Row

    If lastRow < firstDataRow Then
        MsgBox "RssChart の取得データがありません。", vbExclamation
        Exit Sub
    End If

    symbolCode = Trim$(CStr(ws.Range("B6").Value))
    timeframeLabel = Trim$(CStr(ws.Range("B7").Value))
    timeframeCode = Trim$(CStr(ws.Range("D7").Value))
    bars = Trim$(CStr(ws.Range("B8").Value))

    ' RssChartは銘柄名称・市場名称・足種を先頭データ行だけに出す。
    securityName = Trim$(CStr(ws.Cells(firstDataRow, 1).Value))
    marketName = Trim$(CStr(ws.Cells(firstDataRow, 2).Value))

    If Len(securityName) = 0 Then
        If CStr(ws.Range("B4").Value) = "手入力" Then
            securityName = symbolCode
        Else
            securityName = Trim$(CStr(ws.Range("B4").Value))
        End If
    End If

    If Len(timeframeCode) = 0 Then
        timeframeCode = Trim$(CStr(ws.Cells(firstDataRow, 3).Value))
    End If

    If CStr(ws.Range("B4").Value) = "手入力" Then
        targetLabel = securityName
        If Len(targetLabel) = 0 Then targetLabel = symbolCode
    Else
        targetLabel = Trim$(CStr(ws.Range("B4").Value))
    End If

    If Len(ThisWorkbook.Path) > 0 Then
        basePath = ThisWorkbook.Path
    Else
        basePath = CreateObject("WScript.Shell").SpecialFolders("Desktop")
    End If

    fileName = Format(Date, "yyyymmdd") & "_" & _
               CleanFileName(targetLabel) & "_" & _
               CleanFileName(timeframeLabel) & "_" & _
               CleanFileName(bars) & "本.csv"

    fullPath = basePath & Application.PathSeparator & fileName

    n = 2
    Do While Len(Dir(fullPath)) > 0
        fullPath = basePath & Application.PathSeparator & _
                   Left$(fileName, Len(fileName) - 4) & "_" & n & ".csv"
        n = n + 1
    Loop

    oldAlerts = Application.DisplayAlerts
    oldScreenUpdating = Application.ScreenUpdating
    Application.DisplayAlerts = False
    Application.ScreenUpdating = False

    Set outWb = Workbooks.Add(xlWBATWorksheet)
    Set outWs = outWb.Worksheets(1)

    ' 分析用の固定ヘッダー。
    outWs.Range("A1:K1").Value = Array( _
        "銘柄コード", "銘柄名称", "市場", "足種", "日付", "時刻", _
        "始値", "高値", "安値", "終値", "出来高")

    outRow = 2

    For r = firstDataRow To lastRow

        ' RssChart末尾の区切り行は除外。
        If Trim$(CStr(ws.Cells(r, 1).Value)) = "--------" Or _
           Trim$(CStr(ws.Cells(r, 4).Value)) = "--------" Then
            Exit For
        End If

        ' 日付が空の完全空行は出力しない。
        If Len(Trim$(CStr(ws.Cells(r, 4).Text))) = 0 Then
            If Application.WorksheetFunction.CountA(ws.Range(ws.Cells(r, 1), ws.Cells(r, 10))) = 0 Then
                GoTo NextRow
            End If
        End If

        ' 先頭3項目は全行に補完する。
        outWs.Cells(outRow, 1).Value = symbolCode
        outWs.Cells(outRow, 2).Value = securityName
        outWs.Cells(outRow, 3).Value = marketName
        outWs.Cells(outRow, 4).Value = timeframeCode

        ' 日付・時刻・OHLC・出来高。
        outWs.Cells(outRow, 5).Value = ws.Cells(r, 4).Value
        outWs.Cells(outRow, 6).Value = ws.Cells(r, 5).Value
        outWs.Cells(outRow, 7).Value = ws.Cells(r, 6).Value
        outWs.Cells(outRow, 8).Value = ws.Cells(r, 7).Value
        outWs.Cells(outRow, 9).Value = ws.Cells(r, 8).Value
        outWs.Cells(outRow, 10).Value = ws.Cells(r, 9).Value
        outWs.Cells(outRow, 11).Value = ws.Cells(r, 10).Value

        outRow = outRow + 1

NextRow:
    Next r

    If outRow = 2 Then
        outWb.Close SaveChanges:=False
        Application.DisplayAlerts = oldAlerts
        Application.ScreenUpdating = oldScreenUpdating
        MsgBox "保存対象のデータ行がありません。", vbExclamation
        Exit Sub
    End If

    ' CSVを分析しやすい表記へ固定。
    outWs.Columns("A:D").NumberFormat = "@"
    outWs.Columns("E:E").NumberFormat = "yyyy/mm/dd"
    outWs.Columns("F:F").NumberFormat = "hh:mm:ss"
    outWs.Columns("G:J").NumberFormat = "0.########"
    outWs.Columns("K:K").NumberFormat = "0"

    outWb.SaveAs Filename:=fullPath, FileFormat:=xlCSVUTF8, Local:=True
    outWb.Close SaveChanges:=False

    Application.DisplayAlerts = oldAlerts
    Application.ScreenUpdating = oldScreenUpdating

    MsgBox "分析用CSVを保存しました。" & vbCrLf & _
           "データ行数: " & (outRow - 2) & vbCrLf & _
           fullPath, vbInformation
    Exit Sub

ErrHandler:
    On Error Resume Next
    Application.DisplayAlerts = oldAlerts
    Application.ScreenUpdating = oldScreenUpdating
    If Not outWb Is Nothing Then outWb.Close SaveChanges:=False
    MsgBox "CSV保存に失敗しました。" & vbCrLf & Err.Description, vbCritical
End Sub

Public Sub InstallCsvButton()
    Dim ws As Worksheet
    Dim btn As Button

    Set ws = ThisWorkbook.Worksheets("CHART")

    On Error Resume Next
    ws.Buttons("btnExportRssChartCsv").Delete
    On Error GoTo 0

    Set btn = ws.Buttons.Add( _
        ws.Range("H4").Left, _
        ws.Range("H4").Top, _
        ws.Range("H4:J7").Width, _
        ws.Range("H4:J7").Height)

    With btn
        .Name = "btnExportRssChartCsv"
        .Caption = "分析用CSV保存"
        .OnAction = "ExportRssChartCsv"
        .Font.Bold = True
        .Font.Size = 14
    End With

    MsgBox "分析用CSV保存ボタンを作成しました。", vbInformation
End Sub

Private Function FindRssHeaderRow(ByVal ws As Worksheet) As Long
    Dim r As Long

    For r = 11 To 25
        If Trim$(CStr(ws.Cells(r, 1).Value)) = "銘柄名称" And _
           Trim$(CStr(ws.Cells(r, 4).Value)) = "日付" And _
           Trim$(CStr(ws.Cells(r, 10).Value)) = "出来高" Then
            FindRssHeaderRow = r
            Exit Function
        End If
    Next r

    FindRssHeaderRow = 0
End Function

Private Function CleanFileName(ByVal s As String) As String
    Dim badChars As Variant
    Dim ch As Variant

    s = Trim$(s)
    badChars = Array("\", "/", ":", "*", "?", """", "<", ">", "|")

    For Each ch In badChars
        s = Replace(s, CStr(ch), "_")
    Next ch

    s = Replace(s, vbCr, "")
    s = Replace(s, vbLf, "")
    s = Replace(s, vbTab, "_")
    s = Replace(s, " ", "")

    If Len(s) = 0 Then s = "unknown"
    CleanFileName = s
End Function
