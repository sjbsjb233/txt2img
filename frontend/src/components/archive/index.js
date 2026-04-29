/**
 * Archive 卡片组件 — 公共入口
 * ------------------------------------------------------------------
 * 这一组组件实现了 Nano Banana Pro 重设计稿中归档页的所有卡片状态：
 *
 *   ArchiveCard      底层骨架（border + thumb + meta），其它组件复用
 *   RunningCard      渲染中状态（in-flight）— 安静的多重低强度动画
 *   QueuedCard       队列等待中 — 大字号显示队列位置 + ETA
 *   FailCard         失败 / 拒绝 — 静态、不刺眼，可选 retry 按钮
 *   ArchiveSetCard   多图聚合卡 (gpt-image-2 等)，2/3/4/N 自适应布局
 *   ArchiveSetDetail SET 卡片点击后的详情视图
 *
 * 统一使用方式：
 *   import {
 *     ArchiveCard, RunningCard, QueuedCard, FailCard,
 *     ArchiveSetCard, ArchiveSetDetail,
 *   } from "../components/archive";
 *
 * 样式由 src/styles/archive.css 提供，已经在 main.jsx 中全局加载。
 */
export { default as ArchiveCard } from "./ArchiveCard.jsx";
export { default as RunningCard } from "./RunningCard.jsx";
export { default as QueuedCard } from "./QueuedCard.jsx";
export { default as FailCard } from "./FailCard.jsx";
export { default as ArchiveSetCard } from "./ArchiveSetCard.jsx";
export { default as ArchiveSetDetail } from "./ArchiveSetDetail.jsx";
export { default as ArchiveEmptyHero } from "./ArchiveEmptyHero.jsx";
