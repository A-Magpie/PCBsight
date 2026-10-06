# ChatGPT Setup & Compatibility Guide (راهنمای استفاده در چت‌جی‌پی‌تی)

This guide explains how to use **PCBsight** across all ChatGPT interfaces:
1. **Desktop App (Windows / macOS)** via local MCP
2. **Web Browser (chatgpt.com) for Free & Plus users** via Custom GPT (Code Interpreter)
3. **Cloud Remote MCP** for zero-install web plugins

---

## 1. چرا در نسخه وب چت‌جی‌پی‌تی دکمه "Open in desktop app" نمایش داده می‌شود؟

هنگامی که یک پلاگین از نوع **Local MCP** (دستورات محلی پایتون در `.mcp.json`) تعریف می‌شود:
- مرورگر وب شما (`chatgpt.com`) به دلایل امنیتی سندباکس وب اجازه ندارد روی ویندوز یا سیستم شما مستقیماً دستور پایتون یا دسترسی فایل اجرا کند.
- بنابراین چت‌جی‌پی‌تی اعلام می‌کند: این پلاگین برای دسترسی به فایل‌های محلی طراحی شده و باید در **اپلیکیشن دسکتاپ چت‌جی‌پی‌تی** باز شود.

---

## 2. راهکار رایگان و تحت وب برای همه کاربران (ChatGPT Web Free & Plus)

برای اینکه هر کاربری در دنیا (حتی اکانت‌های کاملاً رایگان چت‌جی‌پی‌تی در مرورگر یا موبایل) بتواند بدون نیاز به نصب اپلیکیشن دسکتاپ از PCBsight استفاده کند:

### گام‌های ساخت یک Custom GPT عمومی در ۲ دقیقه:
1. در چت‌جی‌پی‌تی به بخش **Explore GPTs** رفته و روی **Create** کلیک کنید.
2. در تب **Configure**:
   - **Name:** `PCBsight - Altium PCB Analyzer`
   - **Description:** `Inspect, parse, and analyze Altium Designer .PcbDoc files. Extracts layers, tracks, dimensions, clearance rules, DRC violations, and BOM.`
   - **Instructions:** متن زیر را کپی کنید:
     ```markdown
     You are PCBsight, an expert Altium Designer PCB analysis agent.
     When the user uploads a .PcbDoc file:
     1. Unpack the bundled pcbsight package from Knowledge if needed:
        import zipfile, os, sys
        if os.path.exists('pcbsight_bundle.zip') and not os.path.exists('scripts'):
            with zipfile.ZipFile('pcbsight_bundle.zip', 'r') as z:
                z.extractall('.')
        sys.path.insert(0, '.')
        sys.path.insert(0, 'scripts')
        from pcbdoc_parser import PCBDocParser
        from pcb_analyzer import PCBAnalyzer
        from pcb_reporter import PcbReporter

     2. Run the parser on the uploaded .PcbDoc file.
     3. Present a clear, beautifully formatted report including:
        - Board dimensions (width, height, area, outline shape)
        - Full layer stackup (copper layers, dielectric, solder mask, overlay)
        - Placed components and Bill of Materials (BOM) summary
        - Tracks, trace lengths, widths, and routing completeness
        - Clearance design rules and recorded DRC violations
        - Drill schedule and hole bins
     ```
   - **Capabilities:** تیک **Code Interpreter** را فعال کنید.
   - **Knowledge:** فایل آماده **`pcbsight_bundle.zip`** موجود در ریشه ریپازیتوری را آپلود کنید.
3. در بالا سمت راست روی **Save** یا **Update** بزنید و گزینه **Everyone (Public)** را انتخاب کنید.

حالا یک لینک اختصاصی چت‌بات دارید که همه کاربران رایگان چت‌جی‌پی‌تی می‌توانند فایل `.PcbDoc` خود را در مرورگر آپلود کرده و آنالیز کامل تحویل بگیرند!

---

## 3. استفاده در اپلیکیشن دسکتاپ چت‌جی‌پی‌تی (Desktop App)

اگر اپلیکیشن رسمی دسکتاپ چت‌جی‌پی‌تی (ویندوز یا مک) را باز کنید:
1. روی دکمه **Open in desktop app** کلیک کنید یا پوشه ریپازیتوری را در تنطیمات پلاگین‌های دسکتاپ انتخاب کنید.
2. ابزارهای MCP به صورت اتوماتیک متصل می‌شوند:
   - `pcbsight_inspect`
   - `pcbsight_rules`
   - `pcbsight_layers`
   - `pcbsight_bom`
   - `pcbsight_report`

---

## 4. استقرار سرور ابری رایگان (Remote Cloud MCP)

اگر می‌خواهید این پلاگین مستقیماً در مرورگر وب بدون حالت دسکتاپ کار کند:
1. سرور `pcbsight_mcp.py` را روی یک پلتفرم رایگان مثل **Hugging Face Spaces** با دستور زیر ران کنید:
   ```bash
   python pcbsight_mcp.py --http --port 7860
   ```
2. در فایل `.mcp.json` آدرس کلود را قرار دهید:
   ```json
   {
     "mcpServers": {
       "pcbsight": {
         "type": "http",
         "url": "https://your-space.hf.space/mcp"
       }
     }
   }
   ```
این قابلیت در ریپازیتوری تعبیه شده و کاملاً با مشخصات ریموت OpenAI سازگار است.
