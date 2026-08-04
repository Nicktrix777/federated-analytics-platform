package services

import (
	"bytes"
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/xuri/excelize/v2"

	"github.com/federated-analytics/core-api/models"
)

// Excel workbook assembly for report downloads.
//
// The builder receives already-executed sheet results (the handler owns
// execution — this file never talks to the Query Service) and renders a
// formatted workbook: a cover sheet with a linked table of contents, and one
// styled worksheet per report sheet (frozen header, autofilter, zebra
// striping, typed cells with per-column number formats).

// SheetResult pairs a report sheet with its executed query result. A non-empty
// Err means execution failed or was skipped — the sheet still gets a worksheet
// carrying the error so the workbook never silently omits what was asked for.
type SheetResult struct {
	Sheet     models.ReportSheet
	Columns   []string
	Rows      [][]interface{}
	TotalRows int // row count before max_rows truncation
	Err       string
}

// column format vocabulary — shared with the AI planner (models.py
// ALLOWED_COLUMN_FORMATS) and the frontend ColumnFormat type.
const (
	fmtText     = "text"
	fmtInteger  = "integer"
	fmtNumber   = "number"
	fmtCurrency = "currency"
	fmtPercent  = "percent"
	fmtDate     = "date"
	fmtDatetime = "datetime"
)

const (
	coverSheetName = "Report"
	// widthSampleRows bounds how many rows feed the column-width measurement.
	widthSampleRows = 200
	// sniffSampleValues bounds how many values feed per-column type sniffing.
	sniffSampleValues = 50
)

// BuildReportWorkbook renders the full workbook and returns its bytes.
func BuildReportWorkbook(report *models.Report, results []SheetResult) (*bytes.Buffer, error) {
	f := excelize.NewFile()
	defer f.Close()

	if err := f.SetSheetName("Sheet1", coverSheetName); err != nil {
		return nil, fmt.Errorf("failed to create cover sheet: %w", err)
	}

	type tocEntry struct {
		sheetName string
		title     string
		note      string
	}
	toc := make([]tocEntry, 0, len(results))

	used := map[string]bool{strings.ToLower(coverSheetName): true}
	for _, res := range results {
		sheetName := uniqueSheetName(res.Sheet.Title, used)
		if _, err := f.NewSheet(sheetName); err != nil {
			return nil, fmt.Errorf("failed to add sheet %q: %w", res.Sheet.Title, err)
		}

		note := ""
		if res.Err != "" {
			writeErrorSheet(f, sheetName, res)
			note = "failed: " + res.Err
		} else {
			if err := writeDataSheet(f, sheetName, res); err != nil {
				return nil, fmt.Errorf("failed to render sheet %q: %w", res.Sheet.Title, err)
			}
			note = fmt.Sprintf("%d rows", res.TotalRows)
			if res.TotalRows > len(res.Rows) {
				note = fmt.Sprintf("truncated: %d of %d rows", len(res.Rows), res.TotalRows)
			}
		}
		toc = append(toc, tocEntry{sheetName: sheetName, title: res.Sheet.Title, note: note})
	}

	// ── Cover sheet ──
	titleStyle, _ := f.NewStyle(&excelize.Style{Font: &excelize.Font{Bold: true, Size: 16}})
	mutedStyle, _ := f.NewStyle(&excelize.Style{Font: &excelize.Font{Italic: true, Color: "808080"}})
	headStyle, _ := f.NewStyle(&excelize.Style{Font: &excelize.Font{Bold: true}})
	linkStyle, _ := f.NewStyle(&excelize.Style{Font: &excelize.Font{Color: "0563C1", Underline: "single"}})

	f.SetCellValue(coverSheetName, "A1", report.Name)
	f.SetCellStyle(coverSheetName, "A1", "A1", titleStyle)
	f.SetCellValue(coverSheetName, "A2", report.Description)
	f.SetCellValue(coverSheetName, "A3", "Generated "+time.Now().UTC().Format("2006-01-02 15:04:05 UTC"))
	f.SetCellStyle(coverSheetName, "A3", "A3", mutedStyle)

	f.SetCellValue(coverSheetName, "A5", "Sheet")
	f.SetCellValue(coverSheetName, "B5", "Contents")
	f.SetCellStyle(coverSheetName, "A5", "B5", headStyle)
	for i, entry := range toc {
		cell := "A" + strconv.Itoa(6+i)
		f.SetCellValue(coverSheetName, cell, entry.title)
		f.SetCellHyperLink(coverSheetName, cell, "'"+entry.sheetName+"'!A1", "Location")
		f.SetCellStyle(coverSheetName, cell, cell, linkStyle)
		f.SetCellValue(coverSheetName, "B"+strconv.Itoa(6+i), entry.note)
	}
	f.SetColWidth(coverSheetName, "A", "A", 40)
	f.SetColWidth(coverSheetName, "B", "B", 50)

	return f.WriteToBuffer()
}

// writeErrorSheet renders a sheet whose query failed: the error is placed on
// the sheet itself so a downloaded workbook never hides a missing section.
func writeErrorSheet(f *excelize.File, sheetName string, res SheetResult) {
	warnStyle, _ := f.NewStyle(&excelize.Style{Font: &excelize.Font{Bold: true, Color: "9C0006"}})
	f.SetCellValue(sheetName, "A1", "⚠ This sheet could not be generated")
	f.SetCellStyle(sheetName, "A1", "A1", warnStyle)
	f.SetCellValue(sheetName, "A2", res.Err)
	f.SetColWidth(sheetName, "A", "A", 100)
}

// writeDataSheet renders one executed query result as a formatted worksheet.
func writeDataSheet(f *excelize.File, sheetName string, res SheetResult) error {
	formats := resolveColumnFormats(res)

	// Header row.
	for c, name := range res.Columns {
		cell, err := excelize.CoordinatesToCellName(c+1, 1)
		if err != nil {
			return err
		}
		f.SetCellValue(sheetName, cell, name)
	}

	// Data rows, typed per column.
	for r, row := range res.Rows {
		for c := range res.Columns {
			cell, err := excelize.CoordinatesToCellName(c+1, r+2)
			if err != nil {
				return err
			}
			var v interface{}
			if c < len(row) {
				v = row[c]
			}
			setTypedCell(f, sheetName, cell, v, formats[c])
		}
	}

	lastCol, err := excelize.ColumnNumberToName(max(len(res.Columns), 1))
	if err != nil {
		return err
	}
	lastDataRow := len(res.Rows) + 1

	// Header style: bold white on steel blue, wrapped, bordered.
	border := []excelize.Border{
		{Type: "left", Color: "999999", Style: 1},
		{Type: "right", Color: "999999", Style: 1},
		{Type: "top", Color: "999999", Style: 1},
		{Type: "bottom", Color: "999999", Style: 1},
	}
	headerStyle, _ := f.NewStyle(&excelize.Style{
		Font:      &excelize.Font{Bold: true, Color: "FFFFFF"},
		Fill:      excelize.Fill{Type: "pattern", Color: []string{"4472C4"}, Pattern: 1},
		Border:    border,
		Alignment: &excelize.Alignment{Vertical: "center", WrapText: true},
	})
	f.SetCellStyle(sheetName, "A1", lastCol+"1", headerStyle)

	// Per-column number formats over the data range.
	if len(res.Rows) > 0 {
		for c, format := range formats {
			numFmt := numberFormatFor(format, columnValues(res.Rows, c))
			if numFmt == "" {
				continue
			}
			style, _ := f.NewStyle(&excelize.Style{CustomNumFmt: &numFmt})
			colName, err := excelize.ColumnNumberToName(c + 1)
			if err != nil {
				return err
			}
			f.SetCellStyle(sheetName, colName+"2", colName+strconv.Itoa(lastDataRow), style)
		}
	}

	// Freeze the header row and give every column an autofilter.
	f.SetPanes(sheetName, &excelize.Panes{
		Freeze: true, YSplit: 1, TopLeftCell: "A2", ActivePane: "bottomLeft",
	})
	f.AutoFilter(sheetName, "A1:"+lastCol+strconv.Itoa(lastDataRow), nil)

	// Zebra striping via one conditional-format rule over the data range.
	if len(res.Rows) > 0 {
		zebra, _ := f.NewConditionalStyle(&excelize.Style{
			Fill: excelize.Fill{Type: "pattern", Color: []string{"F2F2F2"}, Pattern: 1},
		})
		f.SetConditionalFormat(sheetName, "A2:"+lastCol+strconv.Itoa(lastDataRow),
			[]excelize.ConditionalFormatOptions{
				{Type: "formula", Criteria: "=MOD(ROW(),2)=0", Format: zebra},
			})
	}

	// Column widths from content (header + sampled rows), clamped.
	for c, name := range res.Columns {
		width := float64(len(name)) + 4 // room for the filter dropdown
		for r, row := range res.Rows {
			if r >= widthSampleRows {
				break
			}
			if c < len(row) && row[c] != nil {
				if l := float64(len(fmt.Sprint(row[c]))); l > width {
					width = l
				}
			}
		}
		colName, err := excelize.ColumnNumberToName(c + 1)
		if err != nil {
			return err
		}
		f.SetColWidth(sheetName, colName, colName, clampWidth(width+1))
	}

	// Truncation note under the data when max_rows cut the result short.
	if res.TotalRows > len(res.Rows) {
		noteCell := "A" + strconv.Itoa(lastDataRow+2)
		noteStyle, _ := f.NewStyle(&excelize.Style{Font: &excelize.Font{Italic: true, Color: "808080"}})
		f.SetCellValue(sheetName, noteCell,
			fmt.Sprintf("… truncated: showing %d of %d rows", len(res.Rows), res.TotalRows))
		f.SetCellStyle(sheetName, noteCell, noteCell, noteStyle)
	}

	return nil
}

// resolveColumnFormats returns one format per column: the sheet's curated
// column_formats entry when the alias matches, else a sniffed format from the
// values themselves.
func resolveColumnFormats(res SheetResult) []string {
	declared := map[string]string{}
	if res.Sheet.ColumnFormats != "" {
		_ = json.Unmarshal([]byte(res.Sheet.ColumnFormats), &declared)
	}

	formats := make([]string, len(res.Columns))
	for c, name := range res.Columns {
		if fmtName, ok := declared[name]; ok {
			formats[c] = fmtName
			continue
		}
		formats[c] = sniffColumnFormat(columnValues(res.Rows, c))
	}
	return formats
}

// sniffColumnFormat inspects sampled non-nil values and picks a format:
// all-numeric → integer/number, all-parseable-dates → date/datetime,
// anything else → text.
func sniffColumnFormat(values []interface{}) string {
	seen := 0
	numeric, integral, dates, datetimes := true, true, true, true
	for _, v := range values {
		if v == nil {
			continue
		}
		seen++
		if seen > sniffSampleValues {
			break
		}
		if fv, ok := asFloat(v); ok {
			dates, datetimes = false, false
			if fv != float64(int64(fv)) {
				integral = false
			}
			continue
		}
		numeric, integral = false, false
		if s, ok := v.(string); ok {
			if _, isDate, ok := asTime(s); ok {
				if !isDate {
					dates = false
				}
				continue
			}
		}
		dates, datetimes = false, false
	}
	switch {
	case seen == 0:
		return fmtText
	case numeric && integral:
		return fmtInteger
	case numeric:
		return fmtNumber
	case dates:
		return fmtDate
	case datetimes:
		return fmtDatetime
	default:
		return fmtText
	}
}

// numberFormatFor maps a column format to the Excel number-format string
// applied over the data range. Empty string means no explicit format (text).
func numberFormatFor(format string, values []interface{}) string {
	switch format {
	case fmtInteger:
		return "#,##0"
	case fmtNumber, fmtCurrency:
		return "#,##0.00"
	case fmtPercent:
		// Values already stored as fractions render with Excel's true percent
		// format (display ×100); values that arrive pre-multiplied (e.g. 42.5)
		// keep their magnitude and take a literal % suffix.
		for _, v := range values {
			if fv, ok := asFloat(v); ok && (fv < 0 || fv > 1.5) {
				return `0.00"%"`
			}
		}
		return "0.00%"
	case fmtDate:
		return "yyyy-mm-dd"
	case fmtDatetime:
		return "yyyy-mm-dd hh:mm:ss"
	default:
		return ""
	}
}

// setTypedCell writes one value with the strongest type the column format
// allows: real numbers for numeric columns (including Trino DECIMALs, which
// arrive as strings), real times for date columns, raw value otherwise.
func setTypedCell(f *excelize.File, sheet, cell string, v interface{}, format string) {
	if v == nil {
		return
	}
	switch format {
	case fmtInteger, fmtNumber, fmtCurrency, fmtPercent:
		if fv, ok := asFloat(v); ok {
			f.SetCellValue(sheet, cell, fv)
			return
		}
	case fmtDate, fmtDatetime:
		if s, ok := v.(string); ok {
			if t, _, ok := asTime(s); ok {
				f.SetCellValue(sheet, cell, t)
				return
			}
		}
	}
	f.SetCellValue(sheet, cell, v)
}

// columnValues projects one column out of the row set.
func columnValues(rows [][]interface{}, col int) []interface{} {
	values := make([]interface{}, 0, len(rows))
	for _, row := range rows {
		if col < len(row) {
			values = append(values, row[col])
		}
	}
	return values
}

// asFloat coerces JSON-decoded values (float64) and numeric strings — the
// query service returns Trino DECIMAL/BIGINT-beyond-53-bit values as strings.
func asFloat(v interface{}) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case string:
		s := strings.TrimSpace(n)
		if s == "" {
			return 0, false
		}
		fv, err := strconv.ParseFloat(s, 64)
		return fv, err == nil
	default:
		return 0, false
	}
}

// asTime parses the shapes the query service emits (RFC3339 from
// normalizeValue, plain dates and timestamps from Trino text output).
// isDate reports whether the value carried no time-of-day component.
func asTime(s string) (t time.Time, isDate bool, ok bool) {
	s = strings.TrimSpace(s)
	for _, layout := range []string{"2006-01-02", time.RFC3339, time.RFC3339Nano, "2006-01-02 15:04:05", "2006-01-02T15:04:05"} {
		if parsed, err := time.Parse(layout, s); err == nil {
			return parsed, layout == "2006-01-02", true
		}
	}
	return time.Time{}, false, false
}

// uniqueSheetName sanitizes a title into a legal, workbook-unique Excel sheet
// name: forbidden characters stripped, trimmed to 31 chars, deduplicated with
// a numeric suffix.
func uniqueSheetName(title string, used map[string]bool) string {
	name := strings.Map(func(r rune) rune {
		switch r {
		case '[', ']', ':', '*', '?', '/', '\\', '\'':
			return ' '
		}
		return r
	}, title)
	name = strings.Join(strings.Fields(name), " ") // collapse runs of spaces
	if name == "" {
		name = "Sheet"
	}
	if len(name) > 31 {
		name = strings.TrimSpace(name[:31])
	}

	candidate := name
	for i := 2; used[strings.ToLower(candidate)]; i++ {
		suffix := fmt.Sprintf(" (%d)", i)
		base := name
		if len(base)+len(suffix) > 31 {
			base = strings.TrimSpace(base[:31-len(suffix)])
		}
		candidate = base + suffix
	}
	used[strings.ToLower(candidate)] = true
	return candidate
}

// clampWidth bounds an auto-fitted column width to a readable range.
func clampWidth(w float64) float64 {
	if w < 10 {
		return 10
	}
	if w > 50 {
		return 50
	}
	return w
}
