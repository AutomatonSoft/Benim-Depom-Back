"""Display-only catalog labels. German eBay keys and values remain unchanged."""

from copy import deepcopy
from functools import lru_cache

from apps.catalog.otto_catalog import (
    OttoCatalogError,
    get_otto_catalog,
    get_otto_catalog_translation,
)

# eBay-specific terminology supplements the existing OTTO translations (EN | RU | TR).
TERMS = """
Haus & Garten|Home & garden|Дом и сад|Ev ve bahçe
Möbel & Wohnen|Furniture & living|Мебель и интерьер|Mobilya ve yaşam
Möbel|Furniture|Мебель|Mobilya
Sofas & Sessel|Sofas & armchairs|Диваны и кресла|Kanepeler ve koltuklar
Stühle|Chairs|Стулья|Sandalyeler
Tische & Stehtische|Tables & standing tables|Столы и высокие столы|Masalar ve yüksek masalar
Betten & Bettgestelle|Beds & bed frames|Кровати и каркасы|Yataklar ve yatak çerçeveleri
Kleiderschränke|Wardrobes|Платяные шкафы|Gardıroplar
Schränke & Wandschränke|Cabinets & wall cabinets|Шкафы и навесные шкафы|Dolaplar ve duvar dolapları
Kommoden|Chests of drawers|Комоды|Şifonyerler
Anrichten & Buffets|Sideboards & buffets|Серванты и буфеты|Konsollar ve büfeler
Nachttische & Nachtkonsolen|Bedside tables|Прикроватные тумбы|Komodinler
Regale & Aufbewahrung|Shelving & storage|Стеллажи и хранение|Raflar ve depolama
Sitzbänke|Benches|Скамьи|Banklar
Hocker & Barhocker|Stools & bar stools|Табуреты и барные стулья|Tabureler ve bar tabureleri
Ottomane & Fußhocker|Ottomans & footstools|Оттоманки и пуфы|Puflar ve ayak tabureleri
Schreibtische & Computermöbel|Desks & computer furniture|Письменные и компьютерные столы|Çalışma masaları ve bilgisayar mobilyaları
TV-Möbel & Schrankwände|TV furniture & wall units|ТВ-тумбы и стенки|TV mobilyaları ve duvar üniteleri
Tisch- & Stuhl-Sets|Table & chair sets|Комплекты столов и стульев|Masa ve sandalye takımları
Schlafzimmer-Sets|Bedroom sets|Спальные гарнитуры|Yatak odası takımları
Badmöbelsets|Bathroom furniture sets|Комплекты мебели для ванной|Banyo mobilyası takımları
Schminktische|Dressing tables|Туалетные столики|Makyaj masaları
Servierwagen|Serving trolleys|Сервировочные столики|Servis arabaları
Truhen & Kisten|Chests & boxes|Сундуки и ящики|Sandıklar ve kutular
Lattenroste|Slatted bed bases|Реечные основания|Latali yatak tabanları
Schonbezüge|Furniture covers|Чехлы для мебели|Mobilya kılıfları
Möbelteile|Furniture parts|Детали мебели|Mobilya parçaları
Möbelzubehör|Furniture accessories|Аксессуары для мебели|Mobilya aksesuarları
Betten- & Wasserbettenzubehör|Bed & waterbed accessories|Аксессуары для кроватей|Yatak ve su yatağı aksesuarları
Sitzsäcke & aufblasbare Sessel|Beanbags & inflatable chairs|Кресла-мешки и надувные кресла|Armut koltuklar ve şişme koltuklar
CD- & Video-Regale|CD & video storage|Стеллажи для CD и видео|CD ve video rafları
Wasserbetten|Waterbeds|Водяные кровати|Su yatakları
Futons|Futons|Футоны|Futonlar
Produktart|Product type|Тип товара|Ürün türü
Polsterstoff|Upholstery fabric|Материал обивки|Döşeme kumaşı
Farbe|Colour|Цвет|Renk
Breite|Width|Ширина|Genişlik
Länge|Length|Длина|Uzunluk
Höhe|Height|Высота|Yükseklik
Tiefe|Depth|Глубина|Derinlik
Anzahl der Teile|Number of pieces|Количество предметов|Parça sayısı
Anzahl der Sitzplätze|Number of seats|Количество мест|Oturma kapasitesi
Anzahl der Regalfächer|Number of shelves|Количество полок|Raf sayısı
Anzahl der Räder|Number of wheels|Количество колёс|Tekerlek sayısı
Anzahl der Schubladen|Number of drawers|Количество ящиков|Çekmece sayısı
Anzahl der Türen|Number of doors|Количество дверей|Kapak sayısı
Anzahl der Einheiten|Number of units|Количество единиц|Birim sayısı
Stückzahl|Piece count|Количество штук|Adet
Marke|Brand|Бренд|Marka
Herstellernummer|Manufacturer part number|Артикул производителя|Üretici parça numarası
Herstellergarantie|Manufacturer warranty|Гарантия производителя|Üretici garantisi
Herstellungsjahr|Year of manufacture|Год изготовления|Üretim yılı
Herstellungszeitraum|Production period|Период производства|Üretim dönemi
Ursprungsland|Country of origin|Страна происхождения|Menşe ülke
Füllmaterial|Filling material|Наполнитель|Dolgu malzemesi
Gestellmaterial|Frame material|Материал каркаса|İskelet malzemesi
Tischplattenmaterial|Tabletop material|Материал столешницы|Masa tablası malzemesi
Tischplattenstärke|Tabletop thickness|Толщина столешницы|Masa tablası kalınlığı
Material|Material|Материал|Malzeme
Stil|Style|Стиль|Stil
Zimmer|Room|Комната|Oda
Abteilung|Department|Возрастная группа|Bölüm
Härtegrad|Firmness|Жёсткость|Sertlik
Muster|Pattern|Узор|Desen
Form|Shape|Форма|Şekil
Finish|Finish|Отделка|Yüzey kaplaması
Besonderheiten|Features|Особенности|Özellikler
Pflegeanleitung|Care instructions|Уход|Bakım talimatları
Montage erforderlich|Assembly required|Требуется сборка|Montaj gerekli
Montagezustand|Assembly status|Состояние сборки|Montaj durumu
Montage|Mounting|Крепление|Montaj
Zusätzlich benötigte Teile|Additional parts required|Нужны дополнительные детали|Gerekli ek parçalar
Set enthält|Set includes|Комплект включает|Takım içeriği
Verpackung|Packaging|Упаковка|Ambalaj
Sitzhöhe|Seat height|Высота сиденья|Oturma yüksekliği
Sitztiefe|Seat depth|Глубина сиденья|Oturma derinliği
Armlehnenhöhe|Armrest height|Высота подлокотников|Kolçak yüksekliği
Rückenlehnenhöhe|Backrest height|Высота спинки|Sırtlık yüksekliği
Stil der Armlehne|Armrest style|Тип подлокотников|Kolçak stili
Stil der Rückenlehne|Backrest style|Тип спинки|Sırtlık stili
Bettgröße|Bed size|Размер кровати|Yatak boyutu
Liegefläche|Sleeping area|Спальное место|Yatma alanı
Matratze inklusive|Mattress included|Матрас в комплекте|Yatak dahil
Matratzenart|Mattress type|Тип матраса|Yatak türü
Besonderheiten der Matratze|Mattress features|Особенности матраса|Yatak özellikleri
Kopfteilhöhe|Headboard height|Высота изголовья|Başlık yüksekliği
Fußteilhöhe|Footboard height|Высота изножья|Ayak ucu yüksekliği
Bettkantenhöhe|Bed edge height|Высота края кровати|Yatak kenarı yüksekliği
Unterbetthöhe|Under-bed clearance|Просвет под кроватью|Yatak altı yüksekliği
Abstand vom Fußboden zur Unterseite|Floor clearance|Расстояние от пола до основания|Zemin ile alt yüzey arası mesafe
Durchmesser|Diameter|Диаметр|Çap
Breite (innen)|Internal width|Внутренняя ширина|İç genişlik
Länge (innen)|Internal length|Внутренняя длина|İç uzunluk
Höhe (innen)|Internal height|Внутренняя высота|İç yükseklik
Durchmesser (innen)|Internal diameter|Внутренний диаметр|İç çap
Maximale Breite|Maximum width|Максимальная ширина|Maksimum genişlik
Minimale Breite|Minimum width|Минимальная ширина|Minimum genişlik
Maximale Länge|Maximum length|Максимальная длина|Maksimum uzunluk
Minimale Länge|Minimum length|Минимальная длина|Minimum uzunluk
Maximale Höhe|Maximum height|Максимальная высота|Maksimum yükseklik
Minimale Höhe|Minimum height|Минимальная высота|Minimum yükseklik
Maximale Gewichtskapazität|Maximum weight capacity|Максимальная нагрузка|Maksimum taşıma kapasitesi
Maximales Gewicht pro Regalfach|Maximum weight per shelf|Нагрузка на полку|Raf başına maksimum yük
Gewicht|Weight|Вес|Ağırlık
Größe|Size|Размер|Boyut
Holzton|Wood tone|Оттенок древесины|Ahşap tonu
Griffart|Handle type|Тип ручек|Kulp türü
Griffmaterial|Handle material|Материал ручек|Kulp malzemesi
Griff-Finish|Handle finish|Отделка ручек|Kulp kaplaması
Türart|Door type|Тип дверей|Kapak türü
Türmechanismus|Door mechanism|Механизм дверей|Kapak mekanizması
Rahmenfarbe|Frame colour|Цвет каркаса|Çerçeve rengi
Spiegelform|Mirror shape|Форма зеркала|Ayna şekli
Handgefertigt|Handmade|Ручная работа|El yapımı
Antik|Antique|Антиквариат|Antika
Epoche|Era|Эпоха|Dönem
Original/Reproduktion|Original/reproduction|Оригинал/репродукция|Orijinal/reprodüksiyon
Personalisiert|Personalised|Персонализированный|Kişiselleştirilmiş
Personalisieren|Personalise|Персонализация|Kişiselleştir
Anleitung für Personalisierung|Personalisation instructions|Инструкция по персонализации|Kişiselleştirme talimatları
Produktlinie|Product line|Серия товара|Ürün serisi
Modell|Model|Модель|Model
Markenkompatibilität|Compatible brand|Совместимый бренд|Uyumlu marka
Modellkompatibilität|Compatible model|Совместимая модель|Uyumlu model
Innen-/Außenbereich|Indoor/outdoor|Для помещения/улицы|İç/dış mekan
Geeignet für|Suitable for|Подходит для|Uygun kullanım
Maßeinheit|Unit of measure|Единица измерения|Ölçü birimi
Volumen|Volume|Объём|Hacim
Thema|Theme|Тематика|Tema
Charakter|Character|Персонаж|Karakter
Charakter Familie|Character family|Серия персонажей|Karakter ailesi
CD-/DVD-Kapazität|CD/DVD capacity|Вместимость CD/DVD|CD/DVD kapasitesi
Maximale Bildschirmgröße|Maximum screen size|Максимальный размер экрана|Maksimum ekran boyutu
Minimale Bildschirmgröße|Minimum screen size|Минимальный размер экрана|Minimum ekran boyutu
Smart Home Kommunikationsprotokoll|Smart home protocol|Протокол умного дома|Akıllı ev protokolü
Smart Home Kompatibilität|Smart home compatibility|Совместимость с умным домом|Akıllı ev uyumluluğu
Energieeffizienzklasse|Energy efficiency class|Класс энергоэффективности|Enerji verimliliği sınıfı
EEK Spektrum|Energy label scale|Шкала энергоэффективности|Enerji sınıfı ölçeği
EPREL-Registrierungsnummer|EPREL registration number|Регистрационный номер EPREL|EPREL kayıt numarası
FSC-Zertifizierung|FSC certification|Сертификация FSC|FSC sertifikası
Wasserkern|Water core|Водяная камера|Su çekirdeği
Beruhigung/Stabilisierung|Damping/stabilisation|Стабилизация|Sönümleme/stabilizasyon
Benötigte Werkzeug|Required tools|Необходимые инструменты|Gerekli aletler
Ja|Yes|Да|Evet
Nein|No|Нет|Hayır
Einteilig|One piece|Один предмет|Tek parça
Zweiteilig|Two pieces|Два предмета|İki parça
Dreiteilig|Three pieces|Три предмета|Üç parça
Vierteilig|Four pieces|Четыре предмета|Dört parça
Ecksofa|Corner sofa|Угловой диван|Köşe kanepe
Sofa Set|Sofa set|Комплект диванов|Kanepe takımı
Couchgarnitur|Sofa suite|Диванный гарнитур|Koltuk takımı
Sessel|Armchair|Кресло|Koltuk
Wohnzimmer|Living room|Гостиная|Oturma odası
Schlafzimmer|Bedroom|Спальня|Yatak odası
Esszimmer|Dining room|Столовая|Yemek odası
Schwarz|Black|Чёрный|Siyah
Weiß|White|Белый|Beyaz
Grau|Grey|Серый|Gri
Braun|Brown|Коричневый|Kahverengi
Blau|Blue|Синий|Mavi
Grün|Green|Зелёный|Yeşil
Rot|Red|Красный|Kırmızı
Beige|Beige|Бежевый|Bej
Orange|Orange|Оранжевый|Turuncu
Mehrfarbig|Multicoloured|Разноцветный|Çok renkli
Stoff|Fabric|Ткань|Kumaş
Leder|Leather|Кожа|Deri
100% Leder|100% leather|100% кожа|%100 deri
Kunstleder|Faux leather|Искусственная кожа|Suni deri
Holz|Wood|Дерево|Ahşap
Metall|Metal|Металл|Metal
Marmor|Marble|Мрамор|Mermer
Glas|Glass|Стекло|Cam
"""


@lru_cache(maxsize=3)
def _labels(language):
    labels = {}
    try:
        base = get_otto_catalog()
        translation = get_otto_catalog_translation(language)
        for category in base["categories_by_id"].values():
            labels.setdefault(
                category["name"],
                translation["categories"].get(
                    str(category["categoryId"]), category["name"]
                ),
            )
        for group_id, attributes in base["attributes_by_group_id"].items():
            translated = (
                translation["attribute_groups"]
                .get(str(group_id), {})
                .get("attributes", {})
            )
            for attribute in attributes:
                overlay = translated.get(str(attribute["attributeId"]), {})
                if overlay.get("name"):
                    labels.setdefault(attribute["name"], overlay["name"])
                for original, label in zip(
                    attribute.get("allowedValues", []), overlay.get("allowedValues", [])
                ):
                    labels.setdefault(original, label)
    except OttoCatalogError:
        pass  # eBay-specific labels remain usable if the independent OTTO overlay is unavailable.
    index = {"en": 1, "ru": 2, "tr": 3}[language]
    for line in TERMS.strip().splitlines():
        parts = line.split("|")
        labels[parts[0]] = parts[index]
    return labels


def localize_catalog(payload, language):
    if language == "de":
        return payload
    result = deepcopy(payload)
    labels = _labels(language)
    for category in [
        *result.get("categories", []),
        result.get("category"),
        result.get("parent"),
    ]:
        if category:
            category["label"] = labels.get(category["name"], category["name"])
    for aspect in result.get("aspects", []):
        aspect["label"] = labels.get(
            aspect["localizedAspectName"], aspect["localizedAspectName"]
        )
        for value in aspect.get("aspectValues", []):
            value["label"] = labels.get(
                value["localizedValue"], value["localizedValue"]
            )
    return result
