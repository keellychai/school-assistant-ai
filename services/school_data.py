
import re

SCHOOL_DATA = [
    {
        "id": "SCH001",
        "title": "Mathematics Examination - Grade X",
        "category": "schedule",
        "keywords": ["matematika", "math", "ujian matematika"],
        "content": "Ujian Matematika kelas X dilaksanakan pada 12 November 2026, pukul 08.00–09.30, di ruang kelas masing-masing."
    },
    {
        "id": "SCH002",
        "title": "Indonesian Examination - Grade X",
        "category": "schedule",
        "keywords": ["bahasa indonesia", "ujian bahasa"],
        "content": "Ujian Bahasa Indonesia kelas X dilaksanakan pada 13 November 2026, pukul 08.00–09.30, di ruang kelas masing-masing."
    },
    {
        "id": "SCH003",
        "title": "Flag Ceremony",
        "category": "schedule",
        "keywords": ["upacara", "bendera"],
        "content": "Upacara bendera dilaksanakan pada 2 November 2026 pukul 07.00 di lapangan sekolah."
    },
    {
        "id": "SCH004",
        "title": "School Clean-up Activity",
        "category": "schedule",
        "keywords": ["kerja bakti", "bersih bersih", "sarung tangan"],
        "content": "Kerja bakti sekolah dilaksanakan pada 6 November 2026 pukul 07.30 di halaman sekolah. Peserta membawa sarung tangan."
    },
    {
        "id": "SCH005",
        "title": "Study Tour - Grade XI-A",
        "category": "schedule",
        "keywords": ["studi wisata", "study tour", "xi-a"],
        "content": "Kegiatan studi wisata kelas XI-A dilaksanakan pada 20 November 2026. Peserta berkumpul pukul 06.30."
    },
    {
        "id": "SCH006",
        "title": "Library Card Renewal",
        "category": "announcement",
        "keywords": ["kartu perpustakaan", "perpanjangan kartu", "perpanjang kartu"],
        "content": "Perpanjangan kartu perpustakaan dibuka sampai 30 Oktober 2026 di kantor perpustakaan, pukul 08.00–14.00."
    },
    {
        "id": "SCH007",
        "title": "Science Club Registration",
        "category": "announcement",
        "keywords": ["klub sains", "science club", "pendaftaran sains"],
        "content": "Pendaftaran Klub Sains dibuka sampai 5 November 2026 di laboratorium sains, pukul 14.00–15.00."
    },
    {
        "id": "SCH008",
        "title": "Lost and Found",
        "category": "announcement",
        "keywords": ["barang hilang", "botol minum", "botol biru"],
        "content": "Botol minum biru ditemukan. Informasi dapat ditanyakan di kantor administrasi sampai 30 Oktober 2026."
    },
    {
        "id": "SCH009",
        "title": "Administration Office Hours",
        "category": "administration",
        "keywords": ["kantor administrasi", "jam kantor", "jam administrasi"],
        "content": "Kantor administrasi buka Senin–Jumat, pukul 07.00–15.00. Informasi jam operasional hari Sabtu belum tersedia."
    },
    {
        "id": "SCH010",
        "title": "Late Arrival Procedure",
        "category": "administration",
        "keywords": ["terlambat", "datang terlambat", "telat sekolah"],
        "content": "Siswa yang datang setelah pukul 07.15 harus melapor ke kantor administrasi."
    },
    {
        "id": "SCH011",
        "title": "Study Tour Permission Form",
        "category": "administration",
        "keywords": ["formulir izin", "surat izin", "formulir studi wisata"],
        "content": "Formulir izin studi wisata kelas XI-A harus diserahkan paling lambat 10 November 2026."
    }
]

def normalize(text):
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def search_school_data(question, category=None):
    query = normalize(question)
    query_words = set(query.split())
    results = []

    for item in SCHOOL_DATA:
        title_words = set(normalize(item["title"]).split())
        content_words = set(normalize(item["content"]).split())
        keywords = [normalize(k) for k in item["keywords"]]

        keyword_matches = [
            keyword for keyword in keywords
            if keyword and keyword in query
        ]

        overlap = len(query_words & title_words)
        overlap += len(query_words & content_words) * 0.25

        score = overlap + sum(
            min(len(keyword.split()), 3) * 2
            for keyword in keyword_matches
        )

        if category and item["category"] == category:
            score += 0.5

        if score > 0:
            results.append((score, item))

    results.sort(key=lambda result: result[0], reverse=True)

    if not results:
        return None

    best_score, best_item = results[0]

    if best_score < 2:
        return None

    if len(results) > 1 and best_score - results[1][0] < 0.5:
        if not any(
            normalize(k) in query
            for item in [best_item, results[1][1]]
            for k in item["keywords"]
        ):
            return None

    return best_item
