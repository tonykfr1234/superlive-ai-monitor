<p align="center">
  <a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a> · <a href="README.zh-TW.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a><br>
  <a href="README.de.md">Deutsch</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <a href="README.pt-BR.md">Português (Brasil)</a> · <a href="README.vi.md">Tiếng Việt</a> · <strong>Bahasa Indonesia</strong> · <a href="README.it.md">Italiano</a>
</p>
<p align="center"><img src="assets/superlive-ai.png" width="112" alt="SuperLive AI icon"></p>
<h1 align="center">Deteksi Gerakan Orang dan Kendaraan melalui Tangkapan Layar Kamera Pengawas</h1>
<p align="center">SuperLive AI Monitor · Mendukung emulator Android MuMu dan MEmu</p>
<p align="center">
  <img src="https://img.shields.io/badge/platform-Windows-0078D6" alt="Windows">
  <img src="https://img.shields.io/badge/Python-3.12-3776AB" alt="Python 3.12">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="Lisensi MIT"></a>
</p>
<p align="center"><a href="#quick-start">Mulai</a> · <a href="#workflow">Alur kerja</a> · <a href="#features">Fitur</a> · <a href="#demo">Demo</a> · <a href="#faq">Tanya jawab</a> · <a href="#documentation">Dokumentasi</a></p>

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\INSTALL_FROM_REPO.ps1
```

---

Ikon baru dan rekaman berkualitas tinggi: resolusi tangkapan asli dengan H.264 CRF 18. Telegram mengirim video asli yang sama; hanya berkas di atas batas ukuran yang memakai salinan sementara terkompresi, lalu salinan dihapus setelah berhasil dikirim.

SuperLive AI Monitor menambahkan perekaman berdasarkan kejadian pada tampilan langsung SuperLive di MuMu pada Windows. YOLO mendeteksi orang dan kendaraan di dalam poligon pilihan Anda, lalu video yang selesai dikirim melalui Telegram. Alamat RTSP kamera tidak diperlukan.

Ini adalah proyek komunitas tidak resmi yang tidak berafiliasi dengan penyedia SuperLive atau MuMu. Dua belas bahasa README hanya menerjemahkan dokumentasi; antarmuka pratinjau aplikasi saat ini tetap menggunakan bahasa Tionghoa tradisional.

**Pembaruan 21-10-2026:** Gerakan orang atau kendaraan (mobil, sepeda motor, bus, truk) dapat memicu rekaman. Masa senyap 120 detik dan klip 120–300 detik tetap berlaku; CSV harian dikecualikan. Telegram membedakan orang, jenis kendaraan, dan pelat yang benar-benar terbaca; hasil belum terkonfirmasi ditandai jelas. OCR memindai gambar penuh serta area bertumpang tindih dan pulih otomatis setelah kegagalan. Roda mouse, bilah gulir, dan PageUp/PageDown menampilkan riwayat maksimal tujuh hari, termasuk analisis video asli, tidak terbaca, kandidat belum terkonfirmasi, dan kesalahan.

Secara bawaan, hanya hasil terkonfirmasi yang cocok dengan daftar pribadi hingga dua perubahan karakter yang ditampilkan terbuka; hasil lain disimpan dalam bagian yang dapat dilipat. Klik nama video untuk memilih file asli di Windows File Explorer.

Status OCR, kualitas gambar, dan progres pemrosesan tetap bersama kontrol bawah. Saat pengenalan pelat aktif, hasil yang cocok selalu terlihat di kanan, dikelompokkan menurut tanggal Taiwan dengan jumlah harian dan hasil terbaru di atas. Pada jendela sempit, daftar berada di bawah kamera; rasio gambar tetap terjaga. “其他紀錄” membuka dan menuju catatan lainnya; menutupnya mengembalikan tampilan ke hasil yang cocok.


## Dasbor empat panel

Video langsung di kiri atas, riwayat pelat di kanan atas, foto kendaraan terbaru di kiri bawah, dan semua kontrol di kanan bawah. Seret judul untuk bertukar posisi; tekan L untuk mengatur ulang. Contoh sintetis dirender dengan antarmuka asli.

![Dashboard diagram](docs/media/dashboard-guide.en.png)

[操作說明 / Guide](docs/DASHBOARD.en.md) · [排列動畫 / Layout animation](docs/media/dashboard-layout.gif)

**Instalasi di PC baru:** jalankan `INSTALL.bat` di folder utama, lalu atur ADB, Telegram, dan pelat melalui jendela pengaturan. Panel kiri bawah menampilkan kendaraan dan pelat yang diperbesar berdampingan. [Panduan bahasa Inggris](docs/INSTALL.en.md).

<a id="quick-start"></a>
## Mulai cepat

1. Pasang MuMu dan SuperLive, masuk ke akun, lalu buka kamera yang ingin dipantau. Biarkan sesi pengguna Windows tetap masuk.
2. Klona atau unduh repositori. Jalankan `installer\INSTALL_FROM_REPO.bat` atau perintah PowerShell di atas.
3. Jalankan `installer\SET_TELEGRAM.bat` untuk menyiapkan bot dan chat milik Anda secara lokal. Repositori tidak menyertakan kredensial.
4. Dengan tampilan kamera terbuka, jalankan `installer\VERIFY_ALL.bat` untuk diagnostik tangkapan, berkas, kompilasi Python, dan enkoder.
5. Jalankan `%USERPROFILE%\Downloads\SuperLive_AI\Start_AI_Monitor.bat`; gunakan `Stop_AI_Monitor.bat` di folder yang sama untuk berhenti.

**Sudah terpasang?** Jalankan `installer\UPDATE_FROM_REPO.bat`. Pembaruan mencadangkan dan menghentikan program serta mempertahankan pengaturan lokal area, Telegram, dan penyimpanan. Periksa kamera, lalu mulai kembali secara manual. Tidak dibuat layanan, tugas terjadwal, atau pengaktifan otomatis saat Windows dimulai.

<a id="workflow"></a>
## Alur kerja

```text
Aktivitas orang atau kendaraan baru teramati di dalam poligon
  → mulai merekam, notifikasi Windows, dan gambar bukti lokal
  → 120 detik pertama: tanpa pergantian klip otomatis atau pengiriman Telegram baru
  → gerakan dari detik 120 hingga sebelum 300: akhiri klip lama dan langsung mulai klip baru
  → setiap klip baru memulai lagi masa senyap Telegram selama 120 detik
  → 120 detik terus-menerus tanpa gerakan atau batas 300 detik: akhiri dan tunggu
  → video selesai menunggu di antrean hingga masa senyap saat ini berakhir
```

Orang atau kendaraan yang pertama kali terlihat setelah program dimulai dihitung sebagai aktivitas baru. Klip yang selesai otomatis dalam kondisi normal berdurasi **120–300 detik**. Sebelum detik 120, gerakan mengatur ulang hitungan tanpa gerakan tanpa membagi klip. Mulai detik 120 hingga sebelum batas 300, gerakan mengakhiri klip saat ini dan langsung memulai klip baru. Tidak ada objek atau semua objek diam sama-sama berarti tidak ada gerakan; setelah 120 detik terus-menerus tanpa gerakan, perekaman berhenti dan menunggu gerakan atau kedatangan baru.

**Video selesai, pelat terkonfirmasi, dan laporan CSV harian dikirim otomatis melalui Telegram.** Saat mulai, notifikasi Windows dan bukti lokal tetap dibuat, tanpa foto awal ke Telegram. Tidak ada pengiriman baru yang dimulai selama 120 detik pertama klip saat ini, termasuk video lama dan percobaan ulang. Pergantian klip berturut-turut dapat memperpanjang penantian antrean. Permintaan jaringan yang sudah dikirim sebelum masa senyap masih dapat tiba selama masa tersebut dan tidak dapat ditarik kembali.

Penghentian manual atau kegagalan tangkapan dapat meninggalkan klip parsial lokal di bawah 120 detik. Program tidak menambah gambar untuk membuat durasi palsu; pengiriman otomatis menunggu tenggat senyap klip itu serta masa senyap klip yang lebih baru, jika ada.

Saat memilih area, tangkapan gagal, atau hasil deteksi tidak tersedia, status dianggap belum diketahui. Interval pengamatan diatur ulang, bukan dihitung sebagai tidak ada gerakan yang telah terkonfirmasi.

<a id="features"></a>
## Fitur

| Fitur | Perilaku |
| --- | --- |
| Area | Poligon cembung atau cekung; mendukung pengaturan persegi panjang lama |
| Deteksi | Orang dan kendaraan; kotak deteksi harus beririsan dengan luas positif, menyentuh tepi saja tidak cukup |
| Gambar | Menghapus bilah judul/menu MuMu; mempertahankan waktu dan nama kanal pada gambar kamera |
| Perekaman | Normalnya 120–300 detik; gerakan setelah detik 120 memulai klip baru; berhenti setelah 120 detik terus-menerus tanpa gerakan |
| Pengiriman | Notifikasi dan bukti lokal saat mulai; Telegram mengirim video H.264 MP4 dan laporan CSV harian, antrean dan percobaan ulang menunggu masa senyap berakhir |
| Penyimpanan | 10 FPS; `C:\SuperLive_Recordings`; hapus video lama saat penggunaan disk mencapai 80%, berhenti di 78% |
| Pemulihan | Percobaan tangkapan ulang dan pengawas terpisah untuk pemantau serta perekam |

Gerakan diperkirakan dari perubahan posisi/ukuran kotak orang atau kendaraan, bukan analisis pose. Goyangan kecil disaring dan perpindahan lambat diakumulasikan. Gerakan kamera, objek yang menghalangi, serta kesalahan deteksi tetap dapat memengaruhi hasil. Video menyimpan seluruh gambar kamera yang telah dipotong, bukan hanya bentuk poligonnya.

<a id="demo"></a>
## Demo

Demo **36 detik tanpa suara** ini memakai perender pratinjau aplikasi yang sebenarnya, adegan sintetis, kotak orang yang diatur oleh skrip, serta status perekaman simulasi, dengan teks penjelas dwibahasa. Interval **120 detik dipersingkat** untuk memperlihatkan masa senyap awal Telegram dan pergantian klip akibat gerakan. Ini bukan rekaman kamera nyata atau bukti akurasi YOLO.

![Demo buatan untuk pemilihan area dan perekaman](docs/media/superlive-demo.gif)

[Buka atau unduh video demo MP4](docs/media/superlive-demo.mp4)

| Memilih poligon | Merekam aktivitas | Tidak bergerak dan menunggu pemicu baru |
| --- | --- | --- |
| ![Memilih titik sudut](docs/media/polygon-selection.png) | ![Merekam aktivitas di area](docs/media/activity-recording.png) | ![Menunggu gerakan baru](docs/media/inactivity-rearm.png) |

<a id="controls"></a>
## Kontrol

| Kontrol | Tindakan |
| --- | --- |
| R / pilih area | Mulai membuat poligon |
| Klik kiri | Tambahkan setidaknya tiga titik sudut berbeda mengikuti urutan batas |
| Klik kanan / Backspace | Hapus titik terakhir |
| Enter / simpan | Tutup dan simpan poligon yang memiliki luas serta sisi yang tidak berpotongan |
| Esc / batal | Pertahankan area sebelumnya |
| C / seluruh gambar | Hapus pembatasan area |
| Q / berhenti | Hentikan pemantauan dan selesaikan video saat ini |

Pintasan membutuhkan fokus pada jendela pratinjau. Area disimpan sebagai koordinat relatif terhadap gambar kamera. Jika area lama tidak dapat dikonversi dengan aman, pilih ulang atau pilih seluruh gambar secara eksplisit.

<a id="faq"></a>
## Tanya jawab

**Mengapa orang diam tidak memulai rekaman lagi?** Setelah berhenti karena tidak ada gerakan, diperlukan gerakan atau kedatangan baru. **Berapa lama setiap klip?** Normalnya 120–300 detik; gerakan setelah detik 120 memulai klip baru. Gangguan dapat menghasilkan berkas lokal lebih pendek, tanpa penambahan gambar buatan.

**Bisa berjalan di VMware?** Inferensi CPU didukung. MuMu membutuhkan virtualisasi bersarang yang berfungsi; akselerasi grafis VMware tidak menyediakan CUDA. **Mengapa video belum diterima?** Masa senyap saat ini menunda pengiriman baru dan percobaan ulang, termasuk video lama; pergantian klip berturut-turut memperpanjang penantian. Kegagalan jaringan juga mempertahankan video di antrean. PASS konfigurasi hanya menunjukkan berkas ada; atur kredensial dengan `SET_TELEGRAM.bat` dan periksa penerimaan sebenarnya.

<a id="docs"></a>
<a id="documentation"></a>
## Dokumentasi dan keamanan

[Panduan bahasa Inggris](docs/USER_GUIDE.md) · [Panduan Tionghoa tradisional](docs/USER_GUIDE.zh-TW.md) · [Arsitektur](docs/ARCHITECTURE.md) · [Perekaman](docs/RECORDING.md) · [Perangkat keras](docs/HARDWARE.md) · [VMware](docs/VMWARE.md) · [Perubahan](CHANGELOG.md) · [Keamanan](SECURITY.md)

Kode berada di `src/`, templat konfigurasi di `config/`, dan pemasang di `installer/`. Jangan unggah kredensial, log, gambar kejadian, rekaman, atau cadangan pribadi. Sebelum mengirim perubahan, jalankan `python .\scripts\security_scan.py .`. Pengujian offline tidak menggantikan pemeriksaan dengan kamera dan akun Telegram Anda sendiri.

## Lisensi

[MIT](LICENSE).

## Daily CSV · 23:00 Asia/Taipei

Pukul 23:00 (Taiwan), kirim CSV untuk hari itu mulai 00:00 hingga sebelum 23:00 dan simpan bersama video. CSV harian dikecualikan dari masa senyap rekaman. Kejadian setelah 23:00 tidak disertakan dan tidak ada tambahan tengah malam. Hari tanpa kejadian tetap dilaporkan; kegagalan atau jadwal terlewat dicoba kembali.
