import areaData from 'china-area-data'

type AreaMap = Record<string, Record<string, string>>

export interface RegionOption {
  text: string
  value: string
  children?: RegionOption[]
}

const areas = areaData as AreaMap

const toOptions = (parentCode: string, depth: number): RegionOption[] =>
  Object.entries(areas[parentCode] || {}).map(([value, text]) => {
    const option: RegionOption = { text, value }
    if (depth > 1) {
      const children = toOptions(value, depth - 1)
      if (children.length > 0) option.children = children
    }
    return option
  })

export const regionOptions: RegionOption[] = toOptions('86', 3)

export const findRegionCodes = (names: string[]): string[] => {
  const codes: string[] = []
  let options = regionOptions

  for (const name of names) {
    if (!name) break
    const matched = options.find((item) => item.text === name)
    if (!matched) return []
    codes.push(matched.value)
    options = matched.children || []
  }

  return codes
}
