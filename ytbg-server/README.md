# YT Nền — máy chủ cá nhân (Windows VPS)

Ứng dụng web xem YouTube và nghe nhạc nền, **chỉ dành cho tài khoản do admin cấp**.
Toàn bộ, gồm giao diện, tìm kiếm và luồng âm thanh, đều chạy trên VPS của bạn (Python + [yt-dlp](https://github.com/yt-dlp/yt-dlp)).
Ai không có tài khoản thì chỉ thấy màn hình đăng nhập.

Ứng dụng gồm hai phần:

1. **Xem.** Dùng như app YouTube:
   - trang chủ gồm video mới từ các kênh bạn hay xem, chủ đề bạn chọn, và lịch sử xem;
   - tìm kiếm, trang kênh;
   - trang xem video bằng trình phát YouTube, kèm video liên quan;
   - nút **🎧 Nghe nền** để chỉ phát âm thanh, kể cả khi tắt màn hình.
2. **Thư viện.** Hồ sơ riêng của từng tài khoản:
   - tạo nhiều playlist, đổi tên, sắp xếp, trộn bài, lặp;
   - bài thêm vào playlist **tự tải file âm thanh về máy** người dùng, nghe được cả khi không có mạng;
   - nút "💾 Lưu file" để xuất file `.m4a` ra máy.

Playlist lưu trên máy chủ nên đồng bộ giữa các thiết bị. File đã tải thì lưu riêng trên từng thiết bị.

> Chỉ nên dùng cho cá nhân. Tải hoặc chuyển tiếp nội dung YouTube ngoài trình phát chính thức
> là trái với điều khoản của YouTube.

---

## Cài mới

Cần chuẩn bị:
- một VPS Windows Server 2019 hoặc mới hơn;
- một tên miền trỏ về IP của VPS (ví dụ DuckDNS miễn phí);
- cổng **80 và 443** đã mở ở tường lửa của nhà cung cấp VPS.

1. Tải mã nguồn: <https://github.com/maxicatstudio/maxicatstudio.github.io/archive/refs/heads/main.zip>.
   Giải nén, rồi chép thư mục `ytbg-server` ra `C:\ytbg-server`.
2. Mở **PowerShell bằng quyền Administrator**, rồi chạy:

   ```powershell
   cd C:\ytbg-server
   Set-ExecutionPolicy -Scope Process Bypass -Force
   .\install.ps1 -Domain tenmien.cua.ban
   ```

   Ở lần cài đầu, script hỏi **tên và mật khẩu tài khoản admin**.
3. Mở `https://tenmien.cua.ban` và đăng nhập bằng tài khoản admin. Trên iPhone nên chọn Chia sẻ → **Thêm vào MH chính**.

## Nâng cấp từ bản cũ (bản dùng "khoá bí mật")

Bản cũ có trang chạy trên `maxicatstudio.github.io/ytbg/`. Bản mới chuyển toàn bộ lên máy chủ của bạn.

1. Tải zip mới như bước 1 ở trên, rồi **chép đè** vào `C:\ytbg-server`. Giữ nguyên `config.json`, `cookies.txt` và các thư mục `bin`, `.venv`.
2. Chạy lại `.\install.ps1 -Domain tenmien.cua.ban`. Script hỏi tạo tài khoản admin vì bản cũ chưa có tài khoản.
3. Mở `https://tenmien.cua.ban`. Địa chỉ cũ có `/<khoá>` không dùng nữa.

Từ lần sau, chỉ cần chạy `.\update.ps1 -Code` để lấy bản mới nhất.

## Quản lý tài khoản

Admin vào tab **🛠 Quản trị** trên web để tạo tài khoản, đặt lại mật khẩu, cấp hoặc bỏ quyền admin, và xoá tài khoản.
Có thể làm bằng dòng lệnh trên VPS, trong thư mục `C:\ytbg-server`:

```powershell
.venv\Scripts\python.exe server.py adduser ten_nguoi_dung          # tạo (hỏi mật khẩu)
.venv\Scripts\python.exe server.py adduser ten_admin --admin       # tạo admin
.venv\Scripts\python.exe server.py passwd ten_nguoi_dung           # đổi mật khẩu
.venv\Scripts\python.exe server.py deluser ten_nguoi_dung          # xoá
.venv\Scripts\python.exe server.py users                           # liệt kê
```

Cách hệ thống bảo vệ tài khoản:
- Mật khẩu được mã hoá (PBKDF2).
- Phiên đăng nhập giữ 90 ngày, nằm trong cookie HttpOnly.
- Sai mật khẩu 10 lần trong 15 phút thì tạm khoá theo IP.
- Khi một người đổi mật khẩu, các thiết bị khác của người đó bị đăng xuất.

## Quản lý máy chủ

| Việc | Cách làm |
|---|---|
| Xem log | `C:\ytbg-server\logs\server.log` |
| Khởi động lại | `Stop-ScheduledTask YTNen-Server; Start-ScheduledTask YTNen-Server` |
| Cập nhật yt-dlp | `.\update.ps1` (đã tự chạy mỗi ngày lúc 4:00) |
| Cập nhật ứng dụng | `.\update.ps1 -Code` |
| Sao lưu | chép thư mục `C:\ytbg-server\data` (tài khoản, phiên đăng nhập, playlist, lịch sử) |
| Gỡ cài đặt | `Unregister-ScheduledTask YTNen-Server,YTNen-Caddy,YTNen-Update -Confirm:$false`, rồi xoá thư mục |

## Xử lý sự cố

**Log có dòng "Sign in to confirm you're not a bot", hoặc web báo "YouTube chặn máy chủ".**
YouTube hay chặn IP của VPS. Cách khắc phục:
1. Dùng tiện ích trình duyệt **"Get cookies.txt LOCALLY"** để xuất cookie từ youtube.com. Nên đăng nhập bằng một tài khoản YouTube phụ.
2. Lưu file thành `C:\ytbg-server\cookies.txt`.
3. Khởi động lại máy chủ.

**Bài không phát được.** Chạy `.\update.ps1`. Bản yt-dlp mới thường đã sửa được lỗi do YouTube thay đổi.

**Không vào được web.** Kiểm tra lần lượt:
- tên miền đã trỏ đúng IP chưa (`nslookup tenmien.cua.ban`);
- cổng 80/443 đã mở chưa;
- máy chủ có đang chạy không: mở `http://127.0.0.1:8080` ngay trên VPS.

**Tải về máy bị dừng giữa chừng trên iPhone.** iOS tạm dừng trang khi khoá máy hoặc chuyển app.
Mở lại app thì trang tự tải tiếp.

## Cấu trúc

| Đường dẫn | Nội dung |
|---|---|
| `server.py` | máy chủ: tài khoản, API, chuyển tiếp âm thanh, phục vụ giao diện |
| `web/` | giao diện (HTML/CSS/JS, PWA) |
| `data/users.json` | tài khoản (mật khẩu đã mã hoá) |
| `data/sessions.json` | phiên đăng nhập |
| `data/profiles/<tên>.json` | playlist, lịch sử, chủ đề của từng người |
| `config.json` | cổng, đường dẫn cookies |
