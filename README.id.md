<p align="center">
  <a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a> · <a href="README.zh-TW.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a><br>
  <a href="README.de.md">Deutsch</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <a href="README.pt-BR.md">Português (Brasil)</a> · <a href="README.vi.md">Tiếng Việt</a> · <strong>Bahasa Indonesia</strong> · <a href="README.it.md">Italiano</a>
</p>
<p align="center"><img src="assets/superlive-ai.png" width="112" alt="SuperLive AI icon"></p>
<h1 align="center">SuperLive AI Monitor</h1>
<p align="center">Pilih area poligon, rekam aktivitas orang, dan terima videonya melalui Telegram.</p>
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

Ikon baru dan rekaman berkualitas tinggi: resolusi tangkapan asli dengan H.264 CRF 18. Telegram mengutamakan CRF 20 dan hanya mengompresi lebih lanjut salinan yang melebihi batas pengiriman.

SuperLive AI Monitor menambahkan perekaman berdasarkan kejadian pada tampilan langsung SuperLive di MuMu pada Windows. YOLO mendeteksi orang di dalam poligon pilihan Anda, lalu video yang selesai dikirim melalui Telegram. Alamat RTSP kamera tidak diperlukan.

Ini adalah proyek komunitas tidak resmi yang tidak berafiliasi dengan penyedia SuperLive atau MuMu. Dua belas bahasa README hanya menerjemahkan dokumentasi; antarmuka pratinjau aplikasi saat ini tetap menggunakan bahasa Tionghoa tradisional.

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
Aktivitas orang baru teramati di dalam poligon
  → mulai merekam, notifikasi Windows, dan gambar bukti lokal
  → 120 detik pertama: tanpa pergantian klip otomatis atau pengiriman Telegram baru
  → gerakan dari detik 120 hingga sebelum 300: akhiri klip lama dan langsung mulai klip baru
  → setiap klip baru memulai lagi masa senyap Telegram selama 120 detik
  → 120 detik terus-menerus tanpa gerakan atau batas 300 detik: akhiri dan tunggu
  → video selesai menunggu di antrean hingga masa senyap saat ini berakhir
```

Orang yang pertama kali terlihat setelah program dimulai dihitung sebagai aktivitas baru. Klip yang selesai otomatis dalam kondisi normal berdurasi **120–300 detik**. Sebelum detik 120, gerakan mengatur ulang hitungan tanpa gerakan tanpa membagi klip. Mulai detik 120 hingga sebelum batas 300, gerakan mengakhiri klip saat ini dan langsung memulai klip baru. Tidak ada orang dan orang diam sama-sama berarti tidak ada gerakan; setelah 120 detik terus-menerus tanpa gerakan, perekaman berhenti dan menunggu gerakan atau kedatangan baru.

**Hanya video yang selesai menjadi notifikasi Telegram otomatis.** Saat mulai, notifikasi Windows dan bukti lokal tetap dibuat, tanpa foto awal ke Telegram. Tidak ada pengiriman baru yang dimulai selama 120 detik pertama klip saat ini, termasuk video lama dan percobaan ulang. Pergantian klip berturut-turut dapat memperpanjang penantian antrean. Permintaan jaringan yang sudah dikirim sebelum masa senyap masih dapat tiba selama masa tersebut dan tidak dapat ditarik kembali.

Penghentian manual atau kegagalan tangkapan dapat meninggalkan klip parsial lokal di bawah 120 detik. Program tidak menambah gambar untuk membuat durasi palsu; pengiriman otomatis menunggu tenggat senyap klip itu serta masa senyap klip yang lebih baru, jika ada.

Saat memilih area, tangkapan gagal, atau hasil deteksi tidak tersedia, status dianggap belum diketahui. Interval pengamatan diatur ulang, bukan dihitung sebagai tidak ada gerakan yang telah terkonfirmasi.

<a id="features"></a>
## Fitur

| Fitur | Perilaku |
| --- | --- |
| Area | Poligon cembung atau cekung; mendukung pengaturan persegi panjang lama |
| Deteksi | Hanya orang; kotak deteksi harus beririsan dengan luas positif, menyentuh tepi saja tidak cukup |
| Gambar | Menghapus bilah judul/menu MuMu; mempertahankan waktu dan nama kanal pada gambar kamera |
| Perekaman | Normalnya 120–300 detik; gerakan setelah detik 120 memulai klip baru; berhenti setelah 120 detik terus-menerus tanpa gerakan |
| Pengiriman | Notifikasi dan bukti lokal saat mulai; Telegram mengirim video H.264 MP4 dan laporan CSV harian, antrean dan percobaan ulang menunggu masa senyap berakhir |
| Penyimpanan | 10 FPS; `C:\SuperLive_Recordings`; hapus video lama saat penggunaan disk mencapai 80%, berhenti di 78% |
| Pemulihan | Percobaan tangkapan ulang dan pengawas terpisah untuk pemantau serta perekam |

Gerakan diperkirakan dari perubahan posisi/ukuran kotak orang, bukan analisis pose. Goyangan kecil disaring dan perpindahan lambat diakumulasikan. Gerakan kamera, objek yang menghalangi, serta kesalahan deteksi tetap dapat memengaruhi hasil. Video menyimpan seluruh gambar kamera yang telah dipotong, bukan hanya bentuk poligonnya.

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

Setiap hari pukul 23:00 (Taiwan), CSV berisi waktu pemicu, jenis kejadian, jumlah orang, dan nama video disimpan bersama rekaman hari itu lalu dikirim lewat Telegram, termasuk hari tanpa kejadian. Setelah tengah malam, kejadian setelah 23:00 memicu laporan lengkap; tanpa perubahan tidak dikirim ulang. Masa senyap dihormati dan pengiriman dicoba ulang setelah mulai ulang.
