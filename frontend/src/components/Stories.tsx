import { useState } from 'react';
import { api } from '../lib/api';
import { asText, values, type RecordData, type World } from '../lib/types';
import { Empty } from './Timeline';
export default function Stories({
  world,
  onReference,
  onRefresh,
}: {
  world: World;
  onReference: (id: string) => void;
  onRefresh: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const stories = values(world.stories);
  const segments = values(world.segments);
  const executing = world.lifecycle === 'RUNNING';
  const pendingBard = world.phase === 'BARD' && segments.at(-1)?.status !== 'paused';
  const pending = segments.filter(
    (segment) => !stories.some((story) => story.segment_id === (segment.segment_id ?? segment.id)),
  );
  async function generate(segment: RecordData) {
    const segmentId = String(segment.segment_id ?? segment.id);
    setBusy(segmentId);
    setError(null);
    try {
      await api.generateStory(world.world_id, segmentId);
      onRefresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '故事生成失败');
    } finally {
      setBusy(null);
    }
  }
  return (
    <div className="stories">
      {error && (
        <div role="alert" className="error-banner">
          {error}
        </div>
      )}
      {stories.length === 0 && pending.length === 0 && (
        <Empty>完成一段实验后，吟游诗人将编排有事件引用的故事。</Empty>
      )}
      {stories.map((story, index) => (
        <article className="story-card" key={String(story.story_id ?? story.id ?? index)}>
          <span className="eyebrow">
            {story.view === 'research' ? '全知研究版 · 不回灌角色' : '事件叙事版'} ·{' '}
            {String(story.segment_id ?? '')}
          </span>
          <h3>{asText(story.title ?? `第 ${index + 1} 段`)}</h3>
          {story.composition === 'ordered_chapters' && (
            <p className="muted">分章成稿 · {asText(story.chapter_count)}章</p>
          )}
          {values(story.paragraphs as RecordData[] | undefined).length ? (
            values(story.paragraphs as RecordData[]).map((paragraph, paragraphIndex) => (
              <div className="story-paragraph" key={paragraphIndex}>
                {values(story.chapters as RecordData[] | undefined)
                  .filter((chapter) => chapter.paragraph_start === paragraphIndex)
                  .map((chapter, chapterIndex) => (
                    <h4 key={chapterIndex}>{asText(chapter.title)}</h4>
                  ))}
                <p className="story-prose">{asText(paragraph.text)}</p>
                <References references={paragraph.event_refs} onReference={onReference} />
              </div>
            ))
          ) : (
            <p className="story-prose">
              {asText(story.text ?? story.content ?? story.prose ?? '本段没有叙事正文。')}
            </p>
          )}
          <References references={story.event_refs} onReference={onReference} />
          {story.segment_id === (segments.at(-1)?.segment_id ?? segments.at(-1)?.id) && (
            <button
              className="story-regenerate"
              disabled={busy !== null || executing}
              onClick={() => void generate(story)}
            >
              {busy === story.segment_id
                ? '正在请求…'
                : executing && world.phase === 'BARD'
                  ? '正在编排…'
                  : pendingBard
                    ? '继续编排'
                    : '重编本段'}
            </button>
          )}
        </article>
      ))}
      {pending.map((segment, index) => (
        <article key={String(segment.segment_id ?? segment.id ?? index)} className="story-card">
          <h3>片段 {asText(segment.segment_id ?? segment.id)}</h3>
          <p className="muted">
            {world.phase && world.phase !== 'BARD'
              ? '请先完成当前分段；故事编排不会覆盖尚未完成的行动。'
              : '故事待生成或待继续；已完成章节会保留。'}
          </p>
          <button
            disabled={busy !== null || executing || (!!world.phase && world.phase !== 'BARD')}
            onClick={() => void generate(segment)}
          >
            {busy === String(segment.segment_id ?? segment.id)
              ? '正在请求…'
              : executing && world.phase === 'BARD'
                ? '正在编排…'
                : segment.source_end_seq !== undefined
                  ? '继续编排'
                  : '编排故事'}
          </button>
        </article>
      ))}
    </div>
  );
}

function References({
  references,
  onReference,
}: {
  references: unknown;
  onReference: (id: string) => void;
}) {
  const refs = Array.isArray(references) ? references : [];
  return (
    <div className="story-references">
      {refs.map((ref) => {
        const eventId =
          typeof ref === 'string'
            ? ref
            : typeof ref === 'object' && ref !== null
              ? String((ref as RecordData).event_id ?? (ref as RecordData).id ?? '')
              : '';
        return eventId ? (
          <button className="reference" key={eventId} onClick={() => onReference(eventId)}>
            {eventId}
          </button>
        ) : null;
      })}
    </div>
  );
}
