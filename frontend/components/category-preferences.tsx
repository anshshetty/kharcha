'use client';
import { useState } from 'react';
import { api } from '@/lib/api';
import type { CategoryChoices } from '@/lib/types';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import { Picker } from './common';

export function CategoryPreferences({
  categories,
  initial,
  onSaved,
}: {
  categories: string[];
  initial: CategoryChoices;
  onSaved: () => Promise<void>;
}) {
  const [draft, setDraft] = useState(initial);
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const names = [
    ...new Set([
      ...categories,
      ...draft.fixed_categories,
      ...draft.unavoidable_categories,
      ...draft.project_categories,
    ]),
  ].sort((a, b) => a.localeCompare(b));
  const visible = names.filter((name) =>
    name.toLowerCase().includes(query.toLowerCase()),
  );
  const changed = JSON.stringify(draft) !== JSON.stringify(initial);
  const setGroup = (category: string, group: string) => {
    setSaved(false);
    setDraft((value) => ({
      ...value,
      fixed_categories: [
        ...value.fixed_categories.filter((name) => name !== category),
        ...(group === 'fixed' ? [category] : []),
      ],
      unavoidable_categories: [
        ...value.unavoidable_categories.filter((name) => name !== category),
        ...(group === 'unavoidable' ? [category] : []),
      ],
    }));
  };
  const save = async () => {
    setBusy(true);
    setError('');
    setSaved(false);
    try {
      await api('/financial-context', 'PATCH', draft);
      setSaved(true);
      await onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="category-preferences">
      <h3>Your spending groups</h3>
      <p>
        Regular categories appear in spending insights. Mark a category as fixed
        or unavoidable to keep it separate and exclude it from AI suggestions.
        These choices never remove expenses from your ledger total.
      </p>
      <p>
        Major projects are also separated from your everyday spending target.
        This is independent of whether a cost is fixed or unavoidable.
      </p>
      <Input
        aria-label="Find a category"
        placeholder="Find a category"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <fieldset disabled={busy} className="category-choice-list">
        <legend className="sr-only">
          Category spending groups and major projects
        </legend>
        {visible.map((name) => (
          <div className="category-choice-row" key={name}>
            <strong>{name}</strong>
            <Picker
              label={'Spending group for ' + name}
              value={
                draft.fixed_categories.includes(name)
                  ? 'fixed'
                  : draft.unavoidable_categories.includes(name)
                    ? 'unavoidable'
                    : 'regular'
              }
              onChange={(group) => setGroup(name, group)}
              options={[
                { value: 'regular', label: 'Regular' },
                { value: 'fixed', label: 'Fixed' },
                { value: 'unavoidable', label: 'Unavoidable' },
              ]}
            />
            <label className="category-project-choice">
              <Checkbox
                aria-label={'Major project: ' + name}
                checked={draft.project_categories.includes(name)}
                onCheckedChange={(checked) => {
                  setSaved(false);
                  setDraft((value) => ({
                    ...value,
                    project_categories: checked
                      ? [...value.project_categories, name]
                      : value.project_categories.filter(
                          (category) => category !== name,
                        ),
                  }));
                }}
              />
              Major project
            </label>
          </div>
        ))}
        {!visible.length && (
          <p>No matching categories. Add a new category above.</p>
        )}
      </fieldset>
      <p>
        {draft.fixed_categories.length} fixed ·{' '}
        {draft.unavoidable_categories.length} unavoidable ·{' '}
        {draft.project_categories.length} major-project categories
      </p>
      <div className="settings-actions">
        <Button disabled={busy || !changed} onClick={() => void save()}>
          {busy ? 'Saving…' : 'Save category choices'}
        </Button>
        {changed && <span>Unsaved changes</span>}
        {saved && <output>Category choices saved.</output>}
      </div>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
