# 🏥 Sepsis Cases — Process Mining & BPMN Analysis

> Phân tích quy trình điều trị bệnh **nhiễm khuẩn huyết (Sepsis)** từ dữ liệu thực tế của bệnh viện thông qua **Process Mining**, **Graph Analysis** và **BPMN Visualization**.

---

## 📋 Giới thiệu Dataset

| Thông tin | Chi tiết |
|-----------|---------|
| **Nguồn** | Eindhoven University of Technology |
| **Tác giả** | Felix Mannhardt |
| **DOI** | `doi:10.4121/uuid:915d2bfb-7e84-49ad-a286-dc35f063a460` |
| **Loại** | Real-life Event Log (XES format) |
| **Số ca bệnh** | 1,050 traces |
| **Tổng sự kiện** | 15,214 events |
| **Số hoạt động** | 16 activities |
| **Thuộc tính** | 39 data attributes |

Mỗi **trace** đại diện cho hành trình của một bệnh nhân qua bệnh viện. Dữ liệu được ẩn danh hoá; timestamp được ngẫu nhiên hoá nhưng khoảng thời gian giữa các sự kiện được giữ nguyên.

---

## 📂 Cấu trúc dự án

```
.
├── Sepsis Cases - Event Log.xes   # Dataset gốc (XES format)
├── DATA.xml                        # Metadata
├── build_graph.py                  # Script phân tích graph + centrality
├── draw_bpmn.py                    # Script vẽ BPMN diagram
├── requirements.txt
├── README.md
└── output/
    ├── graph.png                   # Activity graph (màu theo mức độ quan trọng)
    ├── graph.graphml               # Dùng cho Gephi / Cytoscape
    ├── edges.csv                   # Danh sách cạnh + trọng số
    ├── centrality.csv              # Bảng xếp hạng độ quan trọng các activity
    ├── summary.json                # Tóm tắt thống kê
    ├── bpmn_diagram.png            # BPMN process diagram
    └── bpmn_diagram.bpmn           # BPMN 2.0 XML (Camunda / draw.io)
```

---

## 🚀 Cài đặt

**Yêu cầu:** Python 3.11+

```bash
# Tạo virtual environment (khuyến nghị)
python -m venv .venv
source .venv/bin/activate        # macOS/Linux
.venv\Scripts\activate           # Windows

# Cài dependencies
pip install -r requirements.txt
```

---

## ▶️ Chạy phân tích

### 1. Phân tích Activity Graph + Centrality

```bash
python build_graph.py "Sepsis Cases - Event Log.xes"
```

Tuỳ chọn:

```bash
# Giới hạn số trace và lọc cạnh hiếm
python build_graph.py "Sepsis Cases - Event Log.xes" --max-traces 200 --min-edge-count 5
```

### 2. Vẽ BPMN Diagram

```bash
python draw_bpmn.py
```

---

## 📊 Kết quả phân tích

### Top activities theo Importance Score

| Rank | Activity | Events | Betweenness | PageRank | Score |
|------|----------|-------:|------------:|---------:|------:|
| 1 | **CRP** | 3,262 | 0.2524 | 0.2544 | **0.87** |
| 2 | **Leucocytes** | 3,383 | 0.1333 | 0.2081 | **0.71** |
| 3 | **IV Antibiotics** | 823 | 0.3849 | 0.0505 | **0.49** |
| 4 | **Admission NC** | 1,182 | 0.2571 | 0.0902 | **0.46** |
| 5 | **ER Triage** | 1,053 | 0.3675 | 0.0230 | **0.46** |

> **Importance Score** = tổng hợp Betweenness Centrality (35%) + PageRank (35%) + Tần suất (30%)

### Ý nghĩa chỉ số

- **Betweenness** — nằm trên nhiều đường đi nhất; tắc nghẽn ở đây ảnh hưởng toàn bộ quy trình. `IV Antibiotics` và `ER Triage` cao nhất.
- **PageRank** — được nhiều activity khác dẫn vào; `CRP` và `Leucocytes` được gọi đến nhiều nhất.
- **Event count** — khối lượng công việc thực tế; `Leucocytes` (3,383) và `CRP` (3,262) bận nhất.

---

## 🗺️ Quy trình BPMN

```
Start → ER Registration → ER Triage → ER Sepsis Triage
                                              ↓
                                       [XOR Gateway]
                                        ├── Lab Tests (CRP / Leucocytes / LacticAcid) ↺
                                        └── IV Liquid → IV Antibiotics
                                              ↓
                                       [XOR Join] → Admission NC / Admission IC
                                              ↓
                                       [XOR Split] ──→ Release A → Return ER ──┐
                                                   ──→ Release B / C / D ───────┤
                                                                                 └→ End
```

File `output/bpmn_diagram.bpmn` có thể mở trực tiếp bằng:
- **[Camunda Modeler](https://camunda.com/download/modeler/)** — desktop app
- **[draw.io](https://app.diagrams.net)** → *Extras → Edit Diagram* → paste nội dung file

---

## 🔬 Các hoạt động trong dataset

| Activity | Số lần | Mô tả |
|----------|-------:|-------|
| Leucocytes | 3,383 | Xét nghiệm bạch cầu |
| CRP | 3,262 | Xét nghiệm CRP (viêm) |
| LacticAcid | 1,466 | Đo nồng độ axit lactic |
| Admission NC | 1,182 | Nhập viện (khoa thường) |
| ER Triage | 1,053 | Phân loại cấp cứu |
| ER Registration | 1,050 | Đăng ký cấp cứu |
| ER Sepsis Triage | 1,049 | Phân loại sepsis |
| IV Antibiotics | 823 | Truyền kháng sinh |
| IV Liquid | 753 | Truyền dịch |
| Release A | 671 | Xuất viện loại A |
| Return ER | 294 | Quay lại cấp cứu |
| Admission IC | 117 | Nhập viện (ICU) |
| Release B | 56 | Xuất viện loại B |
| Release C | 25 | Xuất viện loại C |
| Release D | 24 | Xuất viện loại D |
| Release E | 6 | Xuất viện loại E |

---

## 🛠️ Công nghệ sử dụng

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![NetworkX](https://img.shields.io/badge/NetworkX-3.x-orange)
![Matplotlib](https://img.shields.io/badge/Matplotlib-3.x-blue)
![Pandas](https://img.shields.io/badge/Pandas-2.x-150458?logo=pandas&logoColor=white)

- **NetworkX** — xây dựng và phân tích graph
- **Matplotlib** — visualize graph và BPMN diagram
- **Pandas** — xử lý và xuất bảng centrality
- **SciPy** — tính PageRank
- **xml.etree** — parse XES/BPMN XML

---

## 📄 License

Dataset gốc được phát hành công khai tại [4TU.ResearchData](https://doi.org/10.4121/uuid:915d2bfb-7e84-49ad-a286-dc35f063a460).  
Code trong repo này: **MIT License**.
