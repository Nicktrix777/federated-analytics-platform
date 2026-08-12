#!/usr/bin/env python3
"""Stands in for a real TallyPrime/Tally.ERP9 instance during development
and testing. TallyPrime is proprietary Windows software with no official
Linux/Docker distribution, so this is what trino-plugins/tally is actually
tested against in this repo — it responds on the same XML-over-HTTP
interface real Tally exposes (POST of a TALLYREQUEST=Export Data envelope
to port 9000), dispatching on <REPORTNAME> to return data shaped like each
of the four entities trino-plugins/tally/TallyEntity.java knows about.

This validates the connector/tunnel plumbing (request routing, response
parsing, type coercion) end to end. It does NOT validate that these are
the exact report names or XML tag names a real Tally installation uses —
that needs a real instance (see TallyEntity.java's class comment). If you
get access to one, compare its actual output against RESPONSES below and
correct anything that differs.
"""
import http.server
import re

RESPONSES = {
    # TAXTYPE is a real field observed on a live TallyPrime export (Master.xml,
    # 2026-08-11) that TallyEntity.LEDGERS never modeled - added here so the
    # dynamic schema discovery path (TallySchemaSampler) has something real
    # beyond the fixed baseline to actually find, not just a happy-path replay
    # of the columns already known.
    "List of Ledgers": b"""<ENVELOPE>
  <LEDGER NAME="Cash">
    <PARENT>Current Assets</PARENT>
    <TAXTYPE>Others</TAXTYPE>
    <OPENINGBALANCE>10000.00</OPENINGBALANCE>
    <CLOSINGBALANCE>15000.00</CLOSINGBALANCE>
  </LEDGER>
  <LEDGER NAME="Sales Account">
    <PARENT>Sales Accounts</PARENT>
    <TAXTYPE>Others</TAXTYPE>
    <OPENINGBALANCE>0.00</OPENINGBALANCE>
    <CLOSINGBALANCE>-50000.00</CLOSINGBALANCE>
  </LEDGER>
  <LEDGER NAME="Acme Supplies Pvt Ltd">
    <PARENT>Sundry Creditors</PARENT>
    <TAXTYPE>Others</TAXTYPE>
    <OPENINGBALANCE>0.00</OPENINGBALANCE>
    <CLOSINGBALANCE>-12500.50</CLOSINGBALANCE>
  </LEDGER>
</ENVELOPE>""",
    "Day Book": b"""<ENVELOPE>
  <VOUCHER>
    <DATE>20260701</DATE>
    <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
    <VOUCHERNUMBER>1001</VOUCHERNUMBER>
    <PARTYLEDGERNAME>Acme Supplies Pvt Ltd</PARTYLEDGERNAME>
    <AMOUNT>25000.00</AMOUNT>
  </VOUCHER>
  <VOUCHER>
    <DATE>20260703</DATE>
    <VOUCHERTYPENAME>Payment</VOUCHERTYPENAME>
    <VOUCHERNUMBER>2001</VOUCHERNUMBER>
    <PARTYLEDGERNAME>Cash</PARTYLEDGERNAME>
    <AMOUNT>-5000.00</AMOUNT>
  </VOUCHER>
</ENVELOPE>""",
    "List of Stock Items": b"""<ENVELOPE>
  <STOCKITEM NAME="Widget A">
    <PARENT>Finished Goods</PARENT>
    <BASEUNITS>Nos</BASEUNITS>
    <CLOSINGBALANCE>150.00</CLOSINGBALANCE>
    <CLOSINGVALUE>75000.00</CLOSINGVALUE>
  </STOCKITEM>
  <STOCKITEM NAME="Widget B">
    <PARENT>Finished Goods</PARENT>
    <BASEUNITS>Nos</BASEUNITS>
    <CLOSINGBALANCE>40.00</CLOSINGBALANCE>
    <CLOSINGVALUE>32000.00</CLOSINGVALUE>
  </STOCKITEM>
</ENVELOPE>""",
    "List of Groups": b"""<ENVELOPE>
  <GROUP NAME="Current Assets">
    <PARENT></PARENT>
  </GROUP>
  <GROUP NAME="Sundry Creditors">
    <PARENT>Current Liabilities</PARENT>
  </GROUP>
  <GROUP NAME="Sales Accounts">
    <PARENT></PARENT>
  </GROUP>
</ENVELOPE>""",
}

REPORTNAME_RE = re.compile(rb"<REPORTNAME>(.*?)</REPORTNAME>")


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        match = REPORTNAME_RE.search(body)
        report_name = match.group(1).decode() if match else None
        response = RESPONSES.get(report_name)

        if response is None:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(
                f"mock-tally: unknown REPORTNAME {report_name!r} - known reports: {list(RESPONSES)}".encode()
            )
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, fmt, *args):
        print("mock-tally:", fmt % args, flush=True)


if __name__ == "__main__":
    print("mock-tally listening on :9000", flush=True)
    http.server.HTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
