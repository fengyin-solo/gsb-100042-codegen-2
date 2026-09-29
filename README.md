# 冷链物流温控管理平台

面向冷链运输的车辆调度、温控监测、冷库作业、签收回单、异常报警与冷链断链追溯的综合物流管理后台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 发运单管理 | `shipment` | 发运单 | 运单编号、发货方、收货方 |
| 温控监测 | `temp_monitor` | 温度记录 | 记录编号、运单编号、当前温度 |
| 车辆调度 | `vehicle` | 冷藏车辆 | 车辆编号、车牌号、车型类别 |
| 司机管理 | `driver` | 驾驶人员 | 司机编号、姓名、驾驶证号 |
| 冷库运营 | `cold_storage` | 冷库库区 | 库区编号、库区名称、设定温度 |
| 装卸作业 | `loading` | 装卸记录 | 记录编号、运单编号、装卸类型 |
| 报警管理 | `alert` | 报警记录 | 报警编号、报警类型、关联设备 |
| 线路规划 | `route` | 运输线路 | 线路编号、始发地、到达地 |
| 制冷机组 | `reefer_unit` | 制冷设备 | 机组编号、所属车辆、机组型号 |
| 油料管理 | `fuel` | 加油记录 | 记录编号、车辆编号、油料类型 |
| 签收回单 | `delivery` | 签收记录 | 签收编号、运单编号、签收人 |
| 断链追溯 | `break_chain` | 断链事件 | 事件编号、运单编号、断链环节 |
| 月台管理 | `dock` | 装卸月台 | 月台编号、月台类型、温层分区 |
| 包装管理 | `package` | 保温包装 | 包装编号、包装类型、保温材料 |
| 通行费用 | `toll` | 过路记录 | 记录编号、车辆编号、收费站名称 |
| 车辆消杀 | `sanitation` | 消杀记录 | 消杀编号、车辆编号、消杀方式 |
| 承运合同 | `contract` | 运输合同 | 合同编号、托运方、承运方 |
| 货运保险 | `insurance` | 保险单 | 保单编号、运单编号、投保险种 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 发运单批量导入

- `POST /api/shipment/imports`：上传 UTF-8 CSV，原文件先写入临时暂存目录，再逐行核对运单编号、发货方、收货方、发运批次。
- `POST /api/shipment/imports/{job_id}/commit`：确认暂存结果；提交时在同一把数据锁内二次查重，冲突行保留原记录。
- `GET /api/shipment/batch-summary`：按当前发运单实时重算发运批次汇总，列表与明细也在导入后重新读取。
- 同一导入任务重复确认是幂等操作；并发确认同一运单时只有一个批次能够新增落库，不会重复生成运单。
