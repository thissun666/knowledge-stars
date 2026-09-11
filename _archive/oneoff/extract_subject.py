import json
import os

# ========== 配置文件 ==========
INPUT_TOPICS_PATH = "k12-data/knowledge_points.json"
INPUT_RELATIONS_PATH = "k12-data/kp_relations.json"
OUTPUT_DIR = "k12-data/split/高中"  # 输出到 split/高中 目录下

# 你要提取的科目列表
TARGET_SUBJECTS = ["数学", "日语"]
TARGET_GRADE_BAND = "高中"

# ==============================

def ensure_output_dir():
    """确保输出目录存在"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

def extract_topics():
    """从全量数据中按学科+学段过滤知识点"""
    print(f"📖 读取 {INPUT_TOPICS_PATH} ...")
    with open(INPUT_TOPICS_PATH, "r", encoding="utf-8") as f:
        all_topics = json.load(f)

    print(f"📊 总共 {len(all_topics)} 个知识点")

    # 按目标条件过滤
    filtered = []
    for topic in all_topics:
        subject = topic.get("subject", "")
        grade_band = topic.get("grade_band", "")
        if subject in TARGET_SUBJECTS and grade_band == TARGET_GRADE_BAND:
            filtered.append(topic)

    print(f"✅ 找到 {len(filtered)} 个匹配的知识点")

    # 按科目分别保存
    for subject in TARGET_SUBJECTS:
        subject_topics = [t for t in filtered if t.get("subject") == subject]
        if not subject_topics:
            print(f"⚠️  {subject} 没有找到任何知识点，跳过")
            continue

        output_path = os.path.join(OUTPUT_DIR, f"{subject}.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(subject_topics, f, ensure_ascii=False, indent=2)

        print(f"✅ 已保存 {subject}.json（{len(subject_topics)} 个知识点）")

    return filtered

def extract_relations(topic_ids):
    """从全量关系数据中，筛选出包含这些知识点的关系"""
    print(f"\n📖 读取 {INPUT_RELATIONS_PATH} ...")
    with open(INPUT_RELATIONS_PATH, "r", encoding="utf-8") as f:
        all_relations = json.load(f)

    print(f"📊 总共 {len(all_relations)} 条关系")

    topic_set = set(topic_ids)
    filtered = []
    for rel in all_relations:
        from_id = rel.get("from_kp_id")
        to_id = rel.get("to_kp_id")
        if from_id in topic_set or to_id in topic_set:
            filtered.append(rel)

    print(f"✅ 找到 {len(filtered)} 条相关关系")

    # 按科目分别保存
    for subject in TARGET_SUBJECTS:
        subject_relations = []
        # 这里简化处理：保存所有匹配的关系到一个文件里
        # 因为关系可能跨科目（比如数学关系关联到物理），这里我们直接保存所有筛选出来的关系
        pass

    # 保存全部相关关系到高中目录（因为关系可能跨科目，不分开）
    output_path = os.path.join(OUTPUT_DIR, "relations.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)

    print(f"✅ 已保存 relations.json（{len(filtered)} 条关系）")

if __name__ == "__main__":
    print("🚀 开始提取高中 数学/日语 数据...")
    ensure_output_dir()

    # 1. 提取知识点
    filtered_topics = extract_topics()

    # 2. 收集所有匹配知识点的 ID
    topic_ids = [t.get("id") for t in filtered_topics if t.get("id")]
    print(f"\n📋 共 {len(topic_ids)} 个知识点 ID")

    # 3. 提取关系
    extract_relations(topic_ids)

    print("\n🎉 全部完成！请检查 k12-data/split/高中/ 目录下的文件。")