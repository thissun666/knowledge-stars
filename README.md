# 知识点星图 Knowledge-Stars

基于知识图谱的中小学学习导航工具: 把小学(Marble)、初中(人教数学)、高中(K12-KGraph)的知识点连成带前置依赖的图, 可视化为星图, 自动定位当前可学的知识点。

## 功能
- 三学段知识图谱可视化 (6000+ 节点, 力导向星图, 可缩放/搜索/定位)
- 前置依赖推理: 自动高亮"当前可学"边界, 掌握一个解锁一片
- 掌握度追踪 (本地 SQLite), 快照保存/恢复
- AI 中文讲解 / 按需翻译 (可选, 点页面右上角 [AI 设置] 选供应商粘贴 Key 即时生效; 小学英文节点已内置 286 条预热译文)

## 快速开始 (零编程环境)
1. 到 [Releases](../../releases) 下载 KnowledgeStars-win64.zip
2. 解压到任意文件夹, 双击 KnowledgeStars.exe (黑窗口是服务本体, 别关; 用完关掉即退出)
3. 浏览器自动打开星图
4. 可选 AI: 点页面右上角 [AI 设置], 选供应商粘贴 Key(或编辑 .env)

进度存档在 data/student_progress.db, 备份它即备份全部学习记录。

## 从源码运行
```
git clone https://github.com/thissun666/knowledge-stars.git
cd knowledge-stars
python -m venv venv
venv\Scripts\pip install -r requirements.txt
python launcher.py
```


数据目录放置约定见 setup_dirs.py (克隆对应数据源并改名为 marble-data / pep-data / k12-data / k12kgraph-hf), 或直接用 Release 安装包。

## 数据来源与许可 (重要)
本项目代码为原创 (MIT)。知识图谱数据来自以下开放数据集, 版权归原来源:

| 目录 | 来源 | 许可 |
|---|---|---|
| marble-data/ | [Marble Skill Taxonomy v1](https://github.com/withmarbleapp/os-taxonomy) | ODbL 1.0 (数据库) / CC BY-SA 4.0 (内容) |
| pep-data/ | 人教版初中数学知识图谱 (循 os-taxonomy 结构整理) | ODbL 1.0 / CC BY-SA 4.0; 教材相关内容仅供学习研究 |
| k12-data/ k12kgraph-hf/ | [K12-KGraph (HuggingFace)](https://huggingface.co/datasets/lhpku20010120/K12-KGraph) | 来源页未附许可文本; 学术数据集, 仅供学习研究, 禁止商业用途 |

Marble 数据要求之署名声明 (在此郑重给出):

> Marble Skill Taxonomy (v1) · © Generative Spark, Inc. (Marble) · https://withmarble.com · licensed under ODbL 1.0 (database) and CC BY-SA 4.0 (content).

依 ODbL, 本项目整合后的图谱数据属衍生数据库, 同样以 ODbL 1.0 开放。

## 免责声明
仅供学习研究, 禁止商业用途。教材相关内容版权归人民教育出版社等原权利人。
