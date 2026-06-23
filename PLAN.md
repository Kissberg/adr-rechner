# ADR 1000-Punkte-Rechner — 实现计划

> **For Hermes:** 使用 subagent-driven-development 逐步实现。

**目标:** 构建一个德文界面的 ADR 1000-Punkte 计算器和 Beförderungspapier 生成器

**架构:** Flask + SQLite + Bootstrap 5 前端，运行在 RPi4 ARM64

**技术栈:** Python 3.11, Flask, SQLite, ReportLab (PDF生成), openpyxl (Excel), PyMuPDF (ADR PDF解析)

---

## 数据库模型

### 表结构

```sql
-- UN 编号数据库
CREATE TABLE un_numbers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    un_number VARCHAR(10) UNIQUE NOT NULL,
    substance_name_de VARCHAR(200),
    substance_name_en VARCHAR(200),
    hazard_class VARCHAR(10),
    packing_group VARCHAR(5),
    transport_category INTEGER,  -- 0-4
    tunnel_code VARCHAR(10),
    special_provisions TEXT,
    points_factor DECIMAL(5,2),   -- 50, 3, 1, 0
    max_quantity_per_transport DECIMAL(10,2),
    adr_version VARCHAR(10),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 客户
CREATE TABLE customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(200) NOT NULL,
    street VARCHAR(200),
    zip VARCHAR(10),
    city VARCHAR(100),
    country VARCHAR(50) DEFAULT 'Deutschland',
    contact VARCHAR(100),
    phone VARCHAR(50),
    email VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 发货地址
CREATE TABLE shipping_addresses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(200) NOT NULL,
    street VARCHAR(200),
    zip VARCHAR(10),
    city VARCHAR(100),
    country VARCHAR(50) DEFAULT 'Deutschland',
    is_default BOOLEAN DEFAULT 0
);

-- 运单
CREATE TABLE shipments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    customer_id INTEGER REFERENCES customers(id),
    shipping_address_id INTEGER REFERENCES shipping_addresses(id),
    total_points DECIMAL(10,2),
    is_exempt BOOLEAN,  -- < 1000 Punkte
    bef_papier_path VARCHAR(500),
    adr_version VARCHAR(10),
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

-- 运单明细
CREATE TABLE shipment_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id INTEGER REFERENCES shipments(id),
    un_number VARCHAR(10),
    substance_name VARCHAR(200),
    quantity DECIMAL(10,3),
    unit VARCHAR(10),  -- kg, L, etc.
    transport_category INTEGER,
    points_factor DECIMAL(5,2),
    item_points DECIMAL(10,2),
    FOREIGN KEY (shipment_id) REFERENCES shipments(id)
);
```

---

## 任务分解

### Phase 1: 项目基础

#### Task 1: 项目结构和依赖
- 创建项目目录结构
- 安装 Flask, ReportLab
- 创建 requirements.txt

#### Task 2: 数据库初始化
- 创建 SQLite 数据库和所有表
- 预置常见 UN 编号数据（约 50 个常用危险品）
- 预置运输类别换算表

#### Task 3: Flask 应用骨架
- 创建 app.py 主入口
- 配置 Flask, 模板, 静态文件
- 基础路由

### Phase 2: 核心功能

#### Task 4: 1000-Punkte 计算引擎
- 根据 UN 编号查询运输类别
- 计算公式: ∑(数量 × 系数)
- 判断是否免检 (< 1000)

#### Task 5: 计算器 Web UI
- 德文界面，PC 屏幕优化
- 输入: UN 编号搜索, 数量, 单位
- 实时计算并显示点数
- 添加/删除行

#### Task 6: 客户选择 & 地址
- 客户下拉搜索
- 发货地址选择
- 本次运输信息

### Phase 3: Beförderungspapier

#### Task 7: PDF 生成 (ReportLab)
- ADR 合规模板
- 包含: 发货人, 收货人, UN号, 物质名称, 类别, 数量, 点数, 隧道代码
- 德文标签

#### Task 8: Beförderungspapier 页面
- 预览生成的 PDF
- 下载按钮
- 历史运单列表

### Phase 4: 数据管理

#### Task 9: 客户管理 CRUD
- 客户列表/搜索/编辑/删除
- Excel 导入 (openpyxl)
- 模板下载

#### Task 10: 发货地址管理
- 地址 CRUD
- 设置默认地址

#### Task 11: UN 编号数据库管理
- UN 编号搜索/浏览
- 手动编辑
- CSV 批量导入

### Phase 5: ADR 更新

#### Task 12: ADR PDF 导入
- 上传 ADR PDF
- PyMuPDF 解析 Table A
- 更新 un_numbers 表
- 版本追踪

### Phase 6: 完善

#### Task 13: 首页 Dashboard
- 运单统计
- 快捷操作

#### Task 14: 样式和德文化
- Bootstrap 5 德文主题
- 表单验证
- 响应式优化 (PC 优先)
